"""Read-only local dashboard over an experiment ledger. It never writes experiment.sqlite3.

The page shows which strategies are active, how each one traded (round-trip
episodes rebuilt from the order ledger), the current positions and operator
state. Equity samples are recorded by this process into its own
``dashboard.sqlite3`` so the trader's write path stays untouched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from quantpilot.paper.config import Policy
from quantpilot.paper.reporting import snapshot
from quantpilot.paper.store import OPEN

LEDGER_FILE = "experiment.sqlite3"
SERIES_FILE = "dashboard.sqlite3"
HOST = "127.0.0.1"
# 8765 is taken by Anki on the operator's PC; never kill a port holder, pick a free one.
DEFAULT_PORT = 8770
SAMPLE_KEYS = ("equity", "cash", "realized", "daily_pnl")
FORCED_SAMPLE_SECONDS = 60
CLOSED = ("win", "loss", "flat")


def open_ledger(path: Path) -> sqlite3.Connection:
    """Open the ledger read-only. Missing ledgers are an error, never created."""

    path = Path(path)
    if not path.is_file():
        raise ValueError("ledger_missing")
    # mode=ro keeps SQLite locking (unlike immutable=1), so live WAL writes stay consistent.
    db = sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, timeout=1.0, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=1")
    return db


class LedgerView:
    """Duck-type of the read side of Store, enough for reporting.snapshot()."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._policy = None

    def get(self, key, default=None):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def orders(self, open_only=False):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM orders ORDER BY at,id")]
        return [r for r in rows if r["state"] in OPEN] if open_only else rows

    def positions(self):
        return [
            dict(r) for r in self.db.execute("SELECT * FROM positions ORDER BY symbol")
        ]

    @property
    def policy(self):
        if self._policy is None:
            try:
                self._policy = Policy.model_validate(self.get("policy"))
            except Exception:
                raise ValueError("policy_unreadable") from None
        return self._policy


@contextmanager
def ledger(path: Path):
    """One short read transaction per use so the trader's WAL checkpoints are never held up."""

    db = open_ledger(path)
    try:
        db.execute("BEGIN")
        try:
            yield LedgerView(db)
        finally:
            try:
                db.execute("COMMIT")
            except sqlite3.Error:
                pass
    finally:
        db.close()


def _order_prefix(trade_id: str) -> str:
    return trade_id.rsplit(":", 1)[0]


def _new_episode(order):
    return {
        "strategy": order["strategy"],
        "symbol": order["symbol"],
        "version": order["version"],
        "entry_ids": [],
        "exit_ids": [],
        "entry_at": None,
        "exit_at": None,
        "quantity": 0,
        "held": 0,
        "entry_price": None,
        "exit_price": None,
        "stop": order["stop"],
        "target": order["target"],
        "gross": 0.0,
        "net": 0.0,
        "adjusted": 0.0,
        "r": None,
        "unrealized": None,
        "outcome": "open",
        "reason": None,
        "_bought": 0,
        "_buy_amount": 0.0,
        "_sold": 0,
        "_sell_amount": 0.0,
        "_gross_known": True,
        "_anomaly": False,
    }


def episodes(view: LedgerView) -> list[dict]:
    """Rebuild round trips by replaying filled orders per symbol.

    Sell orders carry no reference to their entry, but the ledger allows one open
    order per symbol, so reservation order equals fill order and a symbol's held
    quantity returning to zero closes the episode. A sell without an open episode
    or a negative held quantity is reported as an anomaly instead of raising.
    """

    fills: dict[str, list[dict]] = {}
    for t in view.db.execute("SELECT * FROM trades ORDER BY at,id"):
        fills.setdefault(_order_prefix(t["id"]), []).append(dict(t))
    r_budget = float(view.get("initial_capital", 0) or 0) * view.policy.trade_risk
    marks = view.get("marks", {})
    basis = {p["symbol"]: p for p in view.positions()}
    live: dict[str, dict] = {}
    result: list[dict] = []
    for o in view.orders():
        if o["filled"] <= 0:
            continue
        ep = live.get(o["symbol"])
        if ep is None:
            ep = _new_episode(o)
            live[o["symbol"]] = ep
            result.append(ep)
            if o["side"] == "sell":
                ep["_anomaly"] = True
        if o["side"] == "buy":
            if ep["entry_at"] is None:
                ep["entry_at"] = o["at"]
            ep["entry_ids"].append(o["id"])
            ep["held"] += o["filled"]
            ep["_bought"] += o["filled"]
            ep["_buy_amount"] += o["amount"]
        else:
            ep["exit_ids"].append(o["id"])
            ep["held"] -= o["filled"]
            ep["_sold"] += o["filled"]
            ep["_sell_amount"] += o["amount"]
            ep["reason"] = o["reason"]
            ep["exit_at"] = o["at"]
            for t in fills.get(o["id"], []):
                ep["net"] += t["pnl"]
                ep["adjusted"] += t["adjusted_pnl"]
                if t["gross_pnl"] is None:
                    ep["_gross_known"] = False
                else:
                    ep["gross"] += t["gross_pnl"]
                ep["exit_at"] = max(ep["exit_at"], t["at"])
            if ep["held"] <= 0:
                del live[o["symbol"]]
    for ep in result:
        ep["quantity"] = ep["_bought"]
        if ep["_bought"]:
            ep["entry_price"] = ep["_buy_amount"] / ep["_bought"]
        if ep["_sold"]:
            ep["exit_price"] = ep["_sell_amount"] / ep["_sold"]
        if not ep["_gross_known"]:
            ep["gross"] = None
        if ep["_anomaly"] or ep["held"] < 0:
            ep["outcome"] = "anomaly"
        elif ep["held"] > 0:
            ep["outcome"] = "open"
            p = basis.get(ep["symbol"])
            mark = marks.get(ep["symbol"])
            if p and mark is not None:
                ep["unrealized"] = p["quantity"] * mark - p["basis"]
        else:
            ep["outcome"] = (
                "win" if ep["net"] > 0 else "loss" if ep["net"] < 0 else "flat"
            )
        if ep["outcome"] in CLOSED and r_budget > 0:
            ep["r"] = ep["adjusted"] / r_budget
        for key in [k for k in ep if k.startswith("_")]:
            del ep[key]
    return result


def strategy_stats(view: LedgerView, eps: list[dict]) -> list[dict]:
    policy = view.policy
    ids = set(policy.active_strategies) | {e["strategy"] for e in eps}
    weights = view.get("weights", {})
    initial = float(view.get("initial_capital", 0) or 0)
    result = []
    for s in sorted(ids):
        closed = sorted(
            (e for e in eps if e["strategy"] == s and e["outcome"] in CLOSED),
            key=lambda e: (e["exit_at"] or "", e["entry_at"] or ""),
        )
        n = len(closed)
        wins = sum(1 for e in closed if e["outcome"] == "win")
        losses = sum(1 for e in closed if e["outcome"] == "loss")
        total = peak = drawdown = 0.0
        for e in closed:
            total += e["adjusted"]
            peak = max(peak, total)
            drawdown = max(drawdown, peak - total)
        gross_values = [e["gross"] for e in closed]
        r_values = [e["r"] for e in closed if e["r"] is not None]
        result.append(
            {
                "strategy": s,
                "active": s in policy.active_strategies,
                "weight": weights.get(s, 0),
                "round_trips": n,
                "wins": wins,
                "losses": losses,
                "win_rate": wins / n if n else None,
                "avg_net": sum(e["net"] for e in closed) / n if n else None,
                "avg_adjusted": sum(e["adjusted"] for e in closed) / n if n else None,
                "avg_r": sum(r_values) / len(r_values) if r_values else None,
                "sum_gross": (
                    None
                    if any(g is None for g in gross_values)
                    else sum(gross_values)
                ),
                "sum_net": sum(e["net"] for e in closed),
                "sum_adjusted": total,
                "max_drawdown": drawdown / initial if initial else None,
                "open_episodes": sum(
                    1 for e in eps if e["strategy"] == s and e["outcome"] == "open"
                ),
                "anomalies": sum(
                    1 for e in eps if e["strategy"] == s and e["outcome"] == "anomaly"
                ),
            }
        )
    return result


def timeline(view: LedgerView, limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 1000))
    rows = [
        dict(r)
        for r in view.db.execute(
            "SELECT * FROM orders ORDER BY at DESC,id DESC LIMIT ?", (limit,)
        )
    ]
    for o in rows:
        o["fills"] = [
            dict(t)
            for t in view.db.execute(
                "SELECT id,quantity,pnl,adjusted_pnl,gross_pnl,at FROM trades "
                "WHERE substr(id,1,length(?))=? AND substr(id,length(?)+1,1)=':' ORDER BY at,id",
                (o["id"], o["id"], o["id"]),
            )
        ]
    return rows


def audit_tail(view: LedgerView, limit: int = 50, scan: int = 1000) -> list[dict]:
    """Newest first; consecutive identical entries (same kind and payload) fold into one row."""

    rows: list[dict] = []
    for r in view.db.execute(
        "SELECT id,at,kind,payload FROM audit ORDER BY id DESC LIMIT ?", (scan,)
    ):
        if rows and rows[-1]["kind"] == r["kind"] and rows[-1]["_raw"] == r["payload"]:
            rows[-1]["count"] += 1
            rows[-1]["first_at"] = r["at"]
            continue
        if len(rows) == limit:
            break
        try:
            payload = json.loads(r["payload"])
        except ValueError:
            payload = None
        rows.append(
            {
                "id": r["id"],
                "at": r["at"],
                "first_at": r["at"],
                "count": 1,
                "kind": r["kind"],
                "payload": payload,
                "_raw": r["payload"],
            }
        )
    for row in rows:
        del row["_raw"]
    return rows


class SeriesStore:
    """Equity samples owned by the dashboard, kept apart from the experiment ledger."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS equity_samples(at TEXT PRIMARY KEY, "
                "equity REAL NOT NULL, cash REAL NOT NULL, realized REAL NOT NULL, "
                "daily_pnl REAL NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def last(self):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM equity_samples ORDER BY at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def append(self, sample: dict):
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO equity_samples VALUES(?,?,?,?,?)",
                (sample["at"], *(sample[k] for k in SAMPLE_KEYS)),
            )

    def recent(self, limit: int = 2000):
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM equity_samples ORDER BY at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]


def sample_once(ledger_path: Path, series: SeriesStore, now: datetime) -> bool:
    """Record mark-to-market equity when it changed, or at least every minute."""

    with ledger(ledger_path) as view:
        report = snapshot(view)
    sample = {
        "at": now.isoformat(),
        "equity": report["equity"],
        "cash": report["cash"],
        "realized": report["realized_net_pnl"],
        "daily_pnl": report["daily_pnl"],
    }
    last = series.last()
    if (
        last
        and all(last[k] == sample[k] for k in SAMPLE_KEYS)
        and (now - datetime.fromisoformat(last["at"])).total_seconds()
        < FORCED_SAMPLE_SECONDS
    ):
        return False
    series.append(sample)
    return True


class Sampler:
    def __init__(self, ledger_path: Path, series_path: Path, seconds: float, clock=None):
        self.ledger_path = Path(ledger_path)
        self.series_path = Path(series_path)
        self.seconds = seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="paper-dashboard-sampler", daemon=True
        )
        self.samples = 0
        self.last_error = None
        self.last_run_at = None

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=15)

    def run(self):
        series = SeriesStore(self.series_path)
        while not self.stopping.is_set():
            try:
                if sample_once(self.ledger_path, series, self.clock()):
                    self.samples += 1
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
            self.last_run_at = self.clock().isoformat()
            self.stopping.wait(self.seconds)

    def status(self):
        return {
            "running": self.thread.is_alive(),
            "interval_seconds": self.seconds,
            "samples": self.samples,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
        }


def summary(ledger_path: Path, series_path: Path, timeline_limit: int = 200) -> dict:
    ledger_path, series_path = Path(ledger_path), Path(series_path)
    with ledger(ledger_path) as view:
        report = snapshot(view)
        for key in ("research", "ai_failures", "strategy_proposal"):
            report.pop(key, None)
        marks = view.get("marks", {})
        marked_at = view.get("marks_at", {})
        for p in report["positions"]:
            mark = marks.get(p["symbol"])
            p["mark"] = mark
            p["marked_at"] = marked_at.get(p["symbol"])
            p["unrealized"] = (
                p["quantity"] * mark - p["basis"] if mark is not None else None
            )
        eps = episodes(view)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime_dir": str(ledger_path.parent),
            "read_only": True,
            "report": report,
            "active_strategies": list(view.policy.active_strategies),
            "strategies": strategy_stats(view, eps),
            "episodes": sorted(
                eps, key=lambda e: (e["exit_at"] or e["entry_at"] or ""), reverse=True
            ),
            "timeline": timeline(view, timeline_limit),
            "open_orders": view.orders(True),
            "audit": audit_tail(view),
        }
    payload["series"] = SeriesStore(series_path).recent() if series_path.is_file() else []
    return payload


def create_app(runtime_dir: Path, sample_seconds: float = 10, clock=None):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    runtime_dir = Path(runtime_dir)
    ledger_path = runtime_dir / LEDGER_FILE
    series_path = runtime_dir / SERIES_FILE
    sampler = Sampler(ledger_path, series_path, sample_seconds, clock)
    no_store = {"Cache-Control": "no-store"}

    @asynccontextmanager
    async def lifespan(app):
        if sample_seconds > 0:
            sampler.start()
        try:
            yield
        finally:
            sampler.close()

    app = FastAPI(
        title="QuantPilot paper dashboard",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/")
    def page():
        return HTMLResponse(PAGE, headers=no_store)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        from fastapi.responses import Response

        return Response(status_code=204, headers=no_store)

    @app.get("/api/health")
    def health():
        return JSONResponse(
            {
                "status": "ok",
                "ledger_present": ledger_path.is_file(),
                "sampler": sampler.status(),
            },
            headers=no_store,
        )

    @app.get("/api/summary")
    def api_summary(timeline_limit: int = 200):
        try:
            payload = summary(ledger_path, series_path, timeline_limit)
        except ValueError as exc:
            reason = str(exc) if str(exc).replace("_", "").isalnum() else "ledger_unreadable"
            return JSONResponse(
                {"status": "unavailable", "reason": reason},
                status_code=503,
                headers={**no_store, "Retry-After": "5"},
            )
        except sqlite3.Error:
            return JSONResponse(
                {"status": "unavailable", "reason": "ledger_busy"},
                status_code=503,
                headers={**no_store, "Retry-After": "2"},
            )
        payload["sampler"] = sampler.status()
        return JSONResponse(payload, headers=no_store)

    return app


def serve(runtime_dir: Path, port: int = DEFAULT_PORT, sample_seconds: float = 10):
    """Loopback only; takes its own lock and never the trader/worker/reporter locks."""

    import uvicorn

    from quantpilot.paper.cli import process_lock

    runtime_dir = Path(runtime_dir)
    if not (runtime_dir / LEDGER_FILE).is_file():
        raise ValueError("ledger_missing")
    with process_lock(runtime_dir / "dashboard.lock"):
        uvicorn.run(
            create_app(runtime_dir, sample_seconds),
            host=HOST,
            port=port,
            access_log=False,
            log_level="warning",
        )


PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QuantPilot 모의운용 현황</title>
<style>
:root{--bg:#f6f7f9;--card:#ffffff;--ink:#1c2128;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;--up:#0f766e;--down:#b91c1c;--warn:#b45309;--warnbg:#fff7ed;--badbg:#fef2f2}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ec;--muted:#9aa3b2;--line:#2a2f3a;--accent:#7aa2ff;--up:#4fd1c5;--down:#f87171;--warn:#fbbf24;--warnbg:#2a2113;--badbg:#2a1616}}
*{box-sizing:border-box}
body{margin:0;padding:14px 16px 32px;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Malgun Gothic",sans-serif}
h1{font-size:17px;margin:0}
h2{font-size:13px;margin:0 0 8px;color:var(--muted);font-weight:600;letter-spacing:.02em}
.top{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin-bottom:10px}
.top .meta{font-size:12px;color:var(--muted);margin-left:auto}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;border:1px solid var(--line);font-size:12px;white-space:nowrap}
.badge.run{border-color:var(--up);color:var(--up)}.badge.warn{border-color:var(--warn);color:var(--warn)}.badge.bad{border-color:var(--down);color:var(--down)}
#alerts{display:none;margin-bottom:10px}
#alerts .a{padding:8px 12px;border-radius:8px;margin-bottom:6px;font-size:13px;border:1px solid var(--warn);background:var(--warnbg)}
#alerts .a.bad{border-color:var(--down);background:var(--badbg)}
#alerts .a b{margin-right:6px}
#err{display:none;background:var(--down);color:#fff;padding:8px 12px;border-radius:8px;margin-bottom:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.tile .k{font-size:12px;color:var(--muted)}.tile .v{font-size:20px;font-weight:600;margin-top:1px;font-variant-numeric:tabular-nums}
.tile .s{font-size:12px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:10px}
.row{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:10px;margin-bottom:10px}
@media (max-width:900px){.row{grid-template-columns:1fr}}
.row .card{margin:0}
.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}
.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;vertical-align:top}
th:first-child,td:first-child,td.l,th.l{text-align:left}
th{color:var(--muted);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:0}
svg{width:100%;height:170px;display:block}
.chart{position:relative}
.tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;display:none;z-index:2}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
details>summary{cursor:pointer;padding:9px 12px;font-size:13px;font-weight:600;color:var(--muted);list-style:none;display:flex;align-items:center;gap:8px}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:'▸';font-size:11px;width:10px;color:var(--muted)}
details[open]>summary::before{content:'▾'}
details>summary .n{font-weight:400}
details>.body{padding:0 12px 10px}
.tools{display:flex;gap:12px;align-items:center;font-size:12px;color:var(--muted);margin:14px 0 6px}
.tools a{color:var(--accent);cursor:pointer;text-decoration:none}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:13px}
.kv div:nth-child(odd){color:var(--muted);white-space:nowrap}
.wrap{white-space:normal}
.small{font-size:12px}
</style>
</head>
<body>
<div class="top">
  <h1>QuantPilot 모의운용</h1>
  <span id="control" class="badge">-</span>
  <span id="hb" class="badge">-</span>
  <span id="mode" class="badge muted">-</span>
  <span class="meta" id="meta"></span>
</div>
<div id="err"></div>
<div id="alerts"></div>
<div class="grid" id="tiles"></div>
<div class="row">
  <div class="card chart">
    <h2>평가자산 (시가평가, 대시보드 표본)</h2>
    <svg id="curve" viewBox="0 0 800 170" preserveAspectRatio="none" aria-label="평가자산 곡선"></svg>
    <div class="tip" id="tip"></div>
  </div>
  <div class="card">
    <h2>지금 열려 있는 것</h2>
    <div id="live"></div>
  </div>
</div>
<div class="card"><h2>전략별 요약</h2><div class="tbl" id="strats"></div></div>

<div class="tools">가끔 보는 항목 <a id="openAll">모두 펼치기</a> <a id="closeAll">모두 접기</a> <span class="muted">· 펼침 상태는 이 브라우저에 저장됩니다</span></div>
<details id="d-episodes"><summary>왕복 거래 <span class="n" id="n-episodes"></span></summary><div class="body tbl" id="episodes"></div></details>
<details id="d-timeline"><summary>주문 타임라인 <span class="n" id="n-timeline"></span></summary><div class="body tbl" id="timeline"></div></details>
<details id="d-state"><summary>운영 상태 상세</summary><div class="body" id="state"></div></details>
<details id="d-candidates"><summary>후보 종목 상태 <span class="n" id="n-candidates"></span></summary><div class="body tbl" id="candidates"></div></details>
<details id="d-audit"><summary>감사 기록 <span class="n" id="n-audit"></span></summary><div class="body tbl" id="audit"></div></details>
<script>
const $=id=>document.getElementById(id);
const won=v=>v==null?'-':Math.round(v).toLocaleString('ko-KR')+'원';
const pct=v=>v==null?'-':(v*100).toFixed(2)+'%';
const num=(v,d)=>v==null?'-':Number(v).toFixed(d==null?2:d);
const cls=v=>v==null?'':v>0?'up':v<0?'down':'';
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const kst=iso=>{if(!iso)return '-';const d=new Date(iso);if(isNaN(d))return esc(iso);return d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});};
const hm=iso=>{if(!iso)return '-';const d=new Date(iso);if(isNaN(d))return esc(iso);return d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'});};
const age=iso=>{if(!iso)return null;return Math.round((Date.now()-new Date(iso).getTime())/1000);};
const ageText=s=>s==null?'없음':s<90?s+'초 전':s<5400?Math.round(s/60)+'분 전':Math.round(s/3600)+'시간 전';
const KO={opening_range_breakout:'장초반 돌파',trend_pullback:'추세 눌림목',range_reversion:'횡보 되돌림'};
const sname=s=>KO[s]||s;
const SIDE={buy:'매수',sell:'매도'};
function tile(k,v,s,c){return '<div class="tile"><div class="k">'+k+'</div><div class="v '+(c||'')+'">'+v+'</div>'+(s?'<div class="s">'+s+'</div>':'')+'</div>';}
function table(head,rows,empty){if(!rows.length)return '<div class="muted small">'+(empty||'없음')+'</div>';return '<table><thead><tr>'+head.map(h=>'<th class="'+(h.l?'l':'')+'">'+h.t+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>';}
function count(id,n){$(id).textContent=n?'('+n+')':'';}

function alerts(d){
  const r=d.report,out=[];const a=age(r.heartbeat);
  if(r.incident)out.push(['bad','incident',esc(r.incident)+' — trader가 신규 진입을 멈춘 상태입니다. 원인 확인 후 명시적 재개가 필요합니다.']);
  const loss=r.intraday_loss_state||{};
  if(loss.daily_halted)out.push(['bad','손실 한도','당일 손실 한도 도달. 다음 거래일까지 신규 진입 중단.']);
  if(loss.drawdown_halted)out.push(['bad','낙폭 한도','누적 낙폭 한도 도달. 원인 검토와 명시적 재개 필요.']);
  if(r.control==='running'&&(a==null||a>120))out.push(['warn','heartbeat','trader heartbeat가 '+ageText(a)+'입니다. 프로세스가 살아 있는지 Status로 확인하세요.']);
  if(r.collector_error)out.push(['warn','수집 오류',esc(r.collector_error)+' — 분봉 수집이 실패하고 있습니다.']);
  if((r.unverified_symbols||[]).length)out.push(['warn','보호 대기',r.unverified_symbols.map(esc).join(', ')+' — 브로커 수량 대사가 필요합니다.']);
  const q=r.positions.filter(p=>p.quarantined);if(q.length)out.push(['warn','격리 보유분',q.map(p=>esc(p.symbol)).join(', ')+' — 마감 미청산분이 격리되어 있습니다.']);
  if(r.valuation_incomplete)out.push(['warn','평가 미확인','일부 보유분의 최신 시세가 없어 표시 자산에 취득원가가 섞여 있습니다.']);
  const stale=(d.open_orders||[]).filter(o=>age(o.at)>600);
  if(stale.length)out.push(['warn','오래된 미종결 주문',stale.map(o=>esc(o.symbol)+' '+SIDE[o.side]+' '+o.quantity+'주 ('+ageText(age(o.at))+', '+esc(o.state)+')').join(' · ')]);
  if(r.flatten_pending)out.push(['warn','청산 진행 중','flatten 요청이 처리 중입니다. 잔량과 미체결을 확인하세요.']);
  if(d.sampler&&d.sampler.last_error)out.push(['warn','표본 수집',esc(d.sampler.last_error)+' — 자산 곡선 기록이 실패하고 있습니다.']);
  const el=$('alerts');el.innerHTML=out.map(([c,t,m])=>'<div class="a '+c+'"><b>'+t+'</b>'+m+'</div>').join('');el.style.display=out.length?'block':'none';
}

function render(d){
  const r=d.report;
  const ctl=$('control');ctl.textContent=(r.incident?'중단 · ':'')+({running:'운용 중',paused:'일시 정지',flattening:'청산 중',stopped:'정지'}[r.control]||r.control);ctl.className='badge '+(r.incident?'bad':r.control==='running'?'run':r.control==='flattening'?'warn':'');
  const a=age(r.heartbeat),ca=age(r.collector_heartbeat);
  const hb=$('hb');hb.textContent='trader '+ageText(a)+' · 수집 '+ageText(ca);hb.className='badge '+((r.control==='running'&&(a==null||a>120))?'warn':'');
  $('mode').textContent=r.data_mode+' · 정책 v'+r.policy_version+' · '+r.assessment_mode;
  $('meta').textContent='갱신 '+hm(d.generated_at)+' · 읽기 전용 · '+d.runtime_dir.split(/[\\/]/).pop();
  alerts(d);
  const openN=(d.open_orders||[]).length;
  $('tiles').innerHTML=[
    tile('평가자산',won(r.equity),'현금 '+won(r.cash)),
    tile('당일 손익',won(r.daily_pnl),pct(r.daily_return),cls(r.daily_pnl)),
    tile('누적 손익',won(r.cumulative_pnl),pct(r.cumulative_return),cls(r.cumulative_pnl)),
    tile('실현 손익',won(r.realized_net_pnl),'비용 반영',cls(r.realized_net_pnl)),
    tile('보유 / 미종결',r.positions.length+' / '+openN,(r.intraday_loss_state?'손실 예산 잔여 '+won(r.intraday_loss_state.available):'')),
  ].join('');
  drawCurve(d.series,r);
  // live: positions + open orders, compact
  let live='';
  if(r.positions.length){live+=table([{t:'종목',l:1},{t:'전략',l:1},{t:'수량'},{t:'평가손익'},{t:'현재가'},{t:'손절'},{t:'목표'}],
    r.positions.map(p=>'<tr><td class="l">'+esc(p.symbol)+(p.quarantined?' <span class="badge warn">격리</span>':'')+'</td><td class="l">'+esc(sname(p.strategy))+'</td><td>'+p.quantity+'</td><td class="'+cls(p.unrealized)+'">'+won(p.unrealized)+'</td><td>'+(p.mark==null?'-':num(p.mark,0))+'</td><td>'+num(p.stop,0)+'</td><td>'+num(p.target,0)+'</td></tr>'));}
  else live+='<div class="muted small">보유 포지션 없음</div>';
  if(openN){live+='<div style="height:8px"></div>'+table([{t:'미종결 주문',l:1},{t:'종목',l:1},{t:'수량'},{t:'체결'},{t:'지정가'},{t:'상태',l:1},{t:'경과'}],
    d.open_orders.map(o=>'<tr><td class="l">'+SIDE[o.side]+'</td><td class="l">'+esc(o.symbol)+'</td><td>'+o.quantity+'</td><td>'+o.filled+'</td><td>'+num(o.price,0)+'</td><td class="l">'+esc(o.state)+'</td><td>'+ageText(age(o.at))+'</td></tr>'));}
  else live+='<div class="muted small" style="margin-top:6px">미종결 주문 없음</div>';
  $('live').innerHTML=live;
  $('strats').innerHTML=table([{t:'전략',l:1},{t:'상태',l:1},{t:'비중'},{t:'왕복'},{t:'승률'},{t:'평균 R'},{t:'비용 반영 손익'},{t:'슬리피지 보정'},{t:'최대 낙폭'},{t:'진행 중'}],
    d.strategies.map(s=>'<tr><td class="l">'+esc(sname(s.strategy))+' <span class="muted small">'+esc(s.strategy)+'</span></td><td class="l">'+(s.active?'<span class="badge run">활성</span>':'<span class="badge">비활성</span>')+'</td><td>'+pct(s.weight)+'</td><td>'+s.round_trips+(s.round_trips?' <span class="muted">('+s.wins+'승 '+s.losses+'패)</span>':'')+'</td><td>'+pct(s.win_rate)+'</td><td>'+num(s.avg_r)+'</td><td class="'+cls(s.sum_net)+'">'+won(s.sum_net)+'</td><td class="'+cls(s.sum_adjusted)+'">'+won(s.sum_adjusted)+'</td><td>'+pct(s.max_drawdown)+'</td><td>'+s.open_episodes+(s.anomalies?' <span class="down">이상 '+s.anomalies+'</span>':'')+'</td></tr>'),'전략 없음');
  // occasional sections
  count('n-episodes',d.episodes.length);
  $('episodes').innerHTML=table([{t:'전략',l:1},{t:'종목',l:1},{t:'진입',l:1},{t:'청산',l:1},{t:'수량'},{t:'진입가'},{t:'청산가'},{t:'비용 반영'},{t:'슬리피지 보정'},{t:'R'},{t:'결과',l:1},{t:'사유',l:1}],
    d.episodes.map(e=>'<tr><td class="l">'+esc(sname(e.strategy))+'</td><td class="l">'+esc(e.symbol)+'</td><td class="l">'+kst(e.entry_at)+'</td><td class="l">'+kst(e.exit_at)+'</td><td>'+e.quantity+'</td><td>'+num(e.entry_price,0)+'</td><td>'+num(e.exit_price,0)+'</td><td class="'+cls(e.outcome==='open'?e.unrealized:e.net)+'">'+(e.outcome==='open'?won(e.unrealized)+' (평가)':won(e.net))+'</td><td>'+(e.outcome==='open'?'-':won(e.adjusted))+'</td><td>'+num(e.r)+'</td><td class="l">'+({win:'승',loss:'패',flat:'무',open:'진행 중',anomaly:'이상'}[e.outcome]||e.outcome)+'</td><td class="l wrap">'+esc(e.reason)+'</td></tr>'),'아직 청산된 거래가 없습니다');
  count('n-timeline',d.timeline.length);
  $('timeline').innerHTML=table([{t:'예약 시각',l:1},{t:'방향',l:1},{t:'종목',l:1},{t:'전략',l:1},{t:'수량'},{t:'체결'},{t:'지정가'},{t:'손절'},{t:'목표'},{t:'상태',l:1},{t:'체결 손익'},{t:'사유',l:1}],
    d.timeline.map(o=>{const pnl=o.fills.reduce((a,f)=>a+(f.pnl||0),0);return '<tr><td class="l">'+kst(o.at)+'</td><td class="l">'+SIDE[o.side]+'</td><td class="l">'+esc(o.symbol)+'</td><td class="l">'+esc(sname(o.strategy))+'</td><td>'+o.quantity+'</td><td>'+o.filled+'</td><td>'+num(o.price,0)+'</td><td>'+num(o.stop,0)+'</td><td>'+num(o.target,0)+'</td><td class="l">'+esc(o.state)+'</td><td class="'+cls(o.side==='sell'&&o.fills.length?pnl:null)+'">'+(o.side==='sell'&&o.fills.length?won(pnl):'-')+'</td><td class="l wrap">'+esc(o.reason)+'</td></tr>';}),'주문 없음');
  const loss=r.intraday_loss_state;
  $('state').innerHTML='<div class="kv">'
    +'<div>제어</div><div>'+esc(r.control)+(r.flatten_pending?' (청산 진행 중)':'')+'</div>'
    +'<div>incident</div><div>'+esc(r.incident||'없음')+'</div>'
    +'<div>데이터 모드</div><div>'+esc(r.data_mode)+' · 전략 세대 '+esc(r.strategy_generation)+' · 정책 v'+r.policy_version+'</div>'
    +'<div>평가 모드</div><div>'+esc(r.assessment_mode)+'</div>'
    +'<div>활성 전략</div><div>'+d.active_strategies.map(s=>esc(sname(s))).join(', ')+'</div>'
    +'<div>trader heartbeat</div><div>'+kst(r.heartbeat)+'</div>'
    +'<div>수집 heartbeat</div><div>'+kst(r.collector_heartbeat)+(r.collector_error?' · 오류 '+esc(r.collector_error):'')+'</div>'
    +'<div>손실 예산</div><div>'+(loss?('잔여 '+won(loss.available)+' · 예약 '+won(loss.reserved)+(loss.daily_halted?' · 당일 한도 도달':'')+(loss.drawdown_halted?' · 누적 낙폭 한도 도달':'')):'없음')+'</div>'
    +'<div>보호 대기</div><div>'+((r.unverified_symbols||[]).map(esc).join(', ')||'없음')+'</div>'
    +'<div>격리 보유분</div><div>'+(r.positions.filter(p=>p.quarantined).map(p=>esc(p.symbol)).join(', ')||'없음')+'</div>'
    +'<div>후보 출처</div><div>'+esc(r.universe_source||'-')+'</div>'
    +'<div>비용 기준</div><div>'+esc(r.cost_basis)+'</div>'
    +'<div>표본 수집</div><div>'+(d.sampler?((d.sampler.running?'수집 중':'중지')+' · '+d.sampler.samples+'건 · '+(d.sampler.last_error||'오류 없음')):'-')+'</div>'
    +'<div>원장</div><div class="wrap">'+esc(d.runtime_dir)+'</div>'
    +'</div>';
  const cs=r.candidate_status||{};const keys=Object.keys(cs).sort();count('n-candidates',keys.length);
  const groups={};keys.forEach(k=>{(groups[cs[k]]=groups[cs[k]]||[]).push(k);});
  $('candidates').innerHTML=table([{t:'상태',l:1},{t:'종목 수'},{t:'종목',l:1}],Object.keys(groups).sort().map(g=>'<tr><td class="l">'+esc(g)+'</td><td>'+groups[g].length+'</td><td class="l wrap">'+groups[g].map(esc).join(', ')+'</td></tr>'),'후보 없음');
  count('n-audit',d.audit.reduce((a,x)=>a+x.count,0));
  $('audit').innerHTML=table([{t:'시각',l:1},{t:'반복'},{t:'종류',l:1},{t:'내용',l:1}],d.audit.map(x=>'<tr><td class="l">'+kst(x.at)+(x.count>1?'<br><span class="muted">← '+kst(x.first_at)+'</span>':'')+'</td><td>'+(x.count>1?x.count+'회':'')+'</td><td class="l">'+esc(x.kind)+'</td><td class="l wrap small">'+esc(JSON.stringify(x.payload))+'</td></tr>'));
}
let pts=[];
function drawCurve(series,r){
  const svg=$('curve');const W=800,H=170,L=8,R=8,T=12,B=20;pts=[];
  if(!series||series.length<2){svg.innerHTML='<text x="12" y="30" fill="currentColor" font-size="13" opacity=".6">표본이 아직 부족합니다 (서버가 실행 중일 때만 기록됩니다)</text>';return;}
  const ys=series.map(s=>s.equity);const base=r.day_base||null;
  let lo=Math.min.apply(null,ys.concat(base==null?[]:[base])),hi=Math.max.apply(null,ys.concat(base==null?[]:[base]));
  if(hi===lo){hi+=1;lo-=1;}const pad=(hi-lo)*0.08;lo-=pad;hi+=pad;
  const t0=new Date(series[0].at).getTime(),t1=new Date(series[series.length-1].at).getTime()||t0+1;
  const X=t=>L+(W-L-R)*((t-t0)/Math.max(1,t1-t0)),Y=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo));
  pts=series.map(s=>({x:X(new Date(s.at).getTime()),y:Y(s.equity),s}));
  let out='';
  for(let i=0;i<4;i++){const y=T+(H-T-B)*i/3;out+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'" stroke="currentColor" opacity=".08"/>';}
  if(base!=null){const y=Y(base);out+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'" stroke="currentColor" opacity=".45" stroke-dasharray="4 4"/><text x="'+(W-R-2)+'" y="'+(y-4)+'" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">당일 기준 '+won(base)+'</text>';}
  const last=pts[pts.length-1];const up=last.s.equity>=(base==null?ys[0]:base);
  out+='<polyline fill="none" stroke="'+(up?'var(--up)':'var(--down)')+'" stroke-width="2" vector-effect="non-scaling-stroke" points="'+pts.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)).join(' ')+'"/>';
  const imin=ys.indexOf(Math.min.apply(null,ys)),imax=ys.indexOf(Math.max.apply(null,ys));
  // A flat series has one extreme; two labels on the same point would collide.
  (imin===imax?[]:[[imin,'최저'],[imax,'최고']]).forEach(([i,l])=>{const p=pts[i];out+='<circle cx="'+p.x+'" cy="'+p.y+'" r="3" fill="currentColor"/><text x="'+Math.min(p.x+5,W-90)+'" y="'+(l==='최저'?p.y+14:p.y-6)+'" font-size="11" fill="currentColor" opacity=".8">'+l+' '+won(p.s.equity)+'</text>';});
  out+='<circle cx="'+last.x+'" cy="'+last.y+'" r="4" fill="'+(up?'var(--up)':'var(--down)')+'"/>';
  out+='<text x="'+L+'" y="'+(H-6)+'" font-size="11" fill="currentColor" opacity=".6">'+kst(series[0].at)+'</text><text x="'+(W-R)+'" y="'+(H-6)+'" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">'+kst(last.s.at)+' · '+won(last.s.equity)+'</text>';
  out+='<line id="xh" x1="0" x2="0" y1="'+T+'" y2="'+(H-B)+'" stroke="currentColor" opacity=".4" style="display:none"/>';
  svg.innerHTML=out;
}
(function(){const svg=$('curve'),tip=$('tip');
  svg.addEventListener('mousemove',e=>{if(!pts.length)return;const rect=svg.getBoundingClientRect();const x=(e.clientX-rect.left)/rect.width*800;let best=pts[0];for(const p of pts){if(Math.abs(p.x-x)<Math.abs(best.x-x))best=p;}
    const xh=$('xh');if(xh){xh.setAttribute('x1',best.x);xh.setAttribute('x2',best.x);xh.style.display='';}
    tip.style.display='block';tip.innerHTML=kst(best.s.at)+'<br>평가자산 '+won(best.s.equity)+'<br>현금 '+won(best.s.cash)+'<br>당일 '+won(best.s.daily_pnl);
    const px=best.x/800*rect.width;tip.style.left=Math.min(px+12,rect.width-170)+'px';tip.style.top=(best.y/170*rect.height+28)+'px';});
  svg.addEventListener('mouseleave',()=>{tip.style.display='none';const xh=$('xh');if(xh)xh.style.display='none';});
})();
// remember which occasional sections the operator keeps open (per browser only)
(function(){const KEY='qp-dashboard-open';let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
  const all=Array.from(document.querySelectorAll('details'));
  all.forEach(d=>{if(saved[d.id])d.open=true;d.addEventListener('toggle',()=>{saved[d.id]=d.open;try{localStorage.setItem(KEY,JSON.stringify(saved));}catch(e){}});});
  $('openAll').onclick=()=>all.forEach(d=>d.open=true);$('closeAll').onclick=()=>all.forEach(d=>d.open=false);
})();
async function tick(){
  try{const res=await fetch('/api/summary',{cache:'no-store'});const d=await res.json();
    if(!res.ok){$('err').style.display='block';$('err').textContent='원장을 읽을 수 없습니다: '+(d.reason||res.status);}
    else{$('err').style.display='none';render(d);}
  }catch(e){$('err').style.display='block';$('err').textContent='서버 응답 없음: '+e;}
  setTimeout(tick,5000);
}
tick();
</script>
</body>
</html>
"""
