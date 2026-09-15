"""Code-computed PnL and a durable, at-most-once Slack DM outbox."""

from __future__ import annotations
from datetime import datetime, timezone
import json
import urllib.request
from quantpilot.paper.http import open_request
from uuid import uuid5, NAMESPACE_URL

from quantpilot.paper.store import encode


def snapshot(store, now=None):
    from quantpilot.paper.recommendations import propose_concentration
    from quantpilot.paper.latency import headline as latency_headline

    now = now or datetime.now(timezone.utc)
    def fresh(value, seconds=180):
        try:
            age = (now - datetime.fromisoformat(value)).total_seconds()
            return 0 <= age <= seconds
        except (TypeError, ValueError):
            return False

    paper = store.policy.data_mode == "paper_trading"
    reconciled = (not paper or (store.get("reconciliation_complete") is True
                  and fresh(store.get("last_reconciled_at"))))
    marks = store.get("marks", {})
    positions = store.positions()
    incomplete = not reconciled or any(
        p["symbol"] not in marks or (paper and not fresh(store.get("marks_at", {}).get(p["symbol"])))
        for p in positions)
    outbox = {r[0]: r[1] for r in store.db.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state")}
    notifications = ("disabled" if not store.policy.slack_enabled else
                     "reporter_unavailable" if not fresh(store.get("reporter_heartbeat")) else
                     "delivery_unknown" if outbox.get("delivery_unknown", 0) else
                     "pending" if outbox.get("pending", 0) else "ready")
    equity = store.get("cash") + sum(
        p["quantity"] * marks.get(p["symbol"], p["basis"] / p["quantity"])
        for p in positions
    )
    base = store.get("day_base", store.get("initial_capital"))
    initial = store.get("initial_capital")
    strategies = []
    strategy_ids = set(store.policy.active_strategies)
    strategy_ids.update(
        row[0] for row in store.db.execute("SELECT DISTINCT strategy FROM trades")
    )
    strategy_ids.update(p["strategy"] for p in positions)
    for strategy in sorted(strategy_ids):
        row = store.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(pnl),0),COALESCE(SUM(adjusted_pnl),0),SUM(gross_pnl) FROM trades WHERE strategy=?",
            (strategy,),
        ).fetchone()
        strategies.append(
            {
                "strategy": strategy,
                "closed_fill_events": row[0],
                "net_pnl": row[1],
                "cost_adjusted_pnl": row[2],
                "gross_fill_pnl": row[3] if row[0] else 0,
                "weight": store.get("weights", {}).get(strategy, 0),
            }
        )
    failures = [
        dict(row)
        for row in store.db.execute(
            "SELECT id,kind,error FROM jobs WHERE state='failed' ORDER BY at DESC LIMIT 12"
        )
    ]
    return {
        "data_mode": store.policy.data_mode,
        "control": store.get("control"),
        "policy_version": store.policy.version,
        "strategy_generation": store.policy.strategy_generation,
        "supervisor_enabled": store.policy.supervisor_enabled,
        "intraday_loss_state": store.get("intraday_loss_state"),
        "intraday_qualified_versions": {
            s: r.get("version") for s, r in store.get("intraday_admission", {}).items()
        },
        "ai_failures": failures,
        "research": store.get("last_research", {"status": "disabled"}),
        "strategy_proposal": propose_concentration(store),
        "equity": round(equity, 2),
        "cash": round(store.get("cash"), 2),
        "daily_pnl": round(equity - base, 2),
        "daily_return": equity / base - 1,
        "daily_pnl_incomplete": incomplete or (paper and not store.get("day_base_valid", False)),
        "day_base_source": store.get("day_base_source"),
        "cumulative_pnl": round(equity - initial, 2),
        "cumulative_return": equity / initial - 1,
        "realized_net_pnl": round(store.get("realized"), 2),
        "positions": positions,
        "open_orders": len(store.orders(True)),
        "strategies": strategies,
        "assessment_mode": store.get("assessment_mode", "rules_only"),
        "incident": store.get("incident"),
        "heartbeat": store.get("heartbeat"),
        "collector_heartbeat": store.get("collector_heartbeat"),
        "collector_error": store.get("collector_error"),
        "collector_error_counts": store.get("collector_error_counts", {}),
        "collector_last_error": store.get("collector_last_error"),
        "reconciliation_complete": reconciled,
        "reconciliation_reason": store.get("reconciliation_reason"),
        "last_reconciled_at": store.get("last_reconciled_at"),
        "protection_status": ("reconciliation_required" if not reconciled else
                              "no_positions" if not positions else
                              "trader_unavailable" if paper and not fresh(store.get("heartbeat")) else
                              "quarantined_next_session" if any(p["quarantined"] for p in positions) else
                              "monitoring"),
        "notification_status": notifications,
        "reporter_heartbeat": store.get("reporter_heartbeat"),
        "worker_heartbeat": store.get("worker_heartbeat"),
        "outbox_counts": outbox,
        "last_report_superseded": store.get("last_report_superseded", False),
        "valuation_invalidated_before": store.get("valuation_invalidated_before"),
        "universe_source": store.get("universe_source"),
        "flatten_pending": store.get("control") == "flattening",
        "unverified_symbols": store.get("unverified_symbols", []),
        "candidate_status": {
            s: store.get("candidate_status:" + s, "not_evaluated")
            for s in store.get("universe", [])
        },
        "valuation_incomplete": incomplete,
        "latency": latency_headline(store, now),
        "cost_basis": "modeled_fee_tax; slippage_separate",
        "live_trading_enabled": False,
        **operational_diagnostics(store, now),
    }


def render(report, review=""):
    lines = [
        f"QuantPilot {report['data_mode']} 일일 보고",
    ]
    if report["valuation_incomplete"]:
        lines.append("당일·누적 손익과 평가자산: 확인 불가 (대사 또는 평가 근거 미완료)")
    else:
        lines.append("당일 손익: 확인 불가 (직전 거래일 평가 기준 미확인)" if report.get("daily_pnl_incomplete") else
                     f"당일 {report['daily_return']:+.2%} / {report['daily_pnl']:+,.0f}원")
        lines.extend([
            f"누적 {report['cumulative_return']:+.2%} / {report['cumulative_pnl']:+,.0f}원",
            f"평가자산 {report['equity']:,.0f}원 · 현금 {report['cash']:,.0f}원",
        ])
    lines.append("손익은 체결금액에 설정된 수수료·세금 가정을 반영합니다.")
    for s in report["strategies"]:
        gross = s.get("gross_fill_pnl")
        gross_text = f"{gross:+,.0f}원" if gross is not None else "확인 필요"
        lines.append(
            f"{s['strategy']}: 배분 {s['weight']:.0%}, 체결 손익 {gross_text}, 비용 반영 {s['net_pnl']:+,.0f}원, 슬리피지 보정 {s['cost_adjusted_pnl']:+,.0f}원"
        )
    lines.extend(
        [
            f"보유 {len(report['positions'])}종목 · 미종결 주문 {report['open_orders']}건",
            f"평가 모드 {report['assessment_mode']} · 상태 {report['incident'] or report['control']}",
        ]
    )
    if report["valuation_incomplete"]:
        lines.append(
            "표시된 보유수량은 원장 기준입니다. 대사 미완료 시 브로커 잔고를 별도로 확인하세요."
        )
    loss = report.get("intraday_loss_state")
    gates = report.get("entry_gates", {}).get("reasons", [])
    labels = {"manual_pause": "수동 일시정지", "no_signal": "신호 없음", "warming": "데이터 준비",
              "stale_feed": "시세 확인 필요", "reconciliation_required": "대사 필요",
              "invalid_baseline": "손익 기준 미확인", "loss_limit": "손실 한도", "entry_cutoff": "진입 시간 종료",
              "recovery_required": "재시작 검증 중", "session_closed": "장 외 시간"}
    if gates:
        lines.append("진입 제한: " + ", ".join(labels.get(gate, gate) for gate in gates))
    if report.get("feed", {}).get("state") != "disabled":
        feed = report.get("feed", {})
        lines.append(f"시세 수신: {feed.get('state', 'unknown')} · 구독 {feed.get('acked_subscriptions', 0)}/40")
    if report.get("data_quarantine"):
        lines.append("분봉 변경 격리: " + ", ".join(report["data_quarantine"]))
    session_trades = report.get("session_trades")
    if session_trades:
        lines.append(f"당일 청산 체결 {session_trades['closed_fill_events']}건 · 이월분 손익 {session_trades['carry_net_pnl']:+,.0f}원")
    api = report.get("api_budget", {})
    if api.get("state") == "enabled":
        lines.append(f"API 대기 {api.get('queue_depth', 0)}건 · 한도 거부 {api.get('violations', 0)}건")
    if loss:
        lines.append(
            f"신규 진입 잔여 손실 예산 {loss['available']:,.0f}원 · 예약 {loss['reserved']:,.0f}원"
        )
        if loss.get("daily_halted"):
            lines.append("당일 손실 한도 도달: 다음 거래일까지 신규 진입 중단")
        if loss.get("drawdown_halted"):
            lines.append("누적 낙폭 한도 도달: 원인 검토와 명시적 재개 필요")
    if report.get("unverified_symbols"):
        lines.append(
            "보호 대기: "
            + ", ".join(report["unverified_symbols"])
            + " — 브로커 수량 대사 필요"
        )
    lines.append(f"보호 상태: {report.get('protection_status', 'unknown')} · 알림: {report.get('notification_status', 'unknown')}")
    for failure in report.get("ai_failures", []):
        lines.append(f"AI 실패: {failure['id']} · {failure['error']}")
    research = report.get("research", {})
    if research.get("status") != "disabled":
        lines.append(
            f"전략 연구: {research.get('status')} · {research.get('reason',research.get('source_hash',''))}"
        )
    proposal = report.get("strategy_proposal")
    if proposal:
        lines.append(
            "전략 축소 제안: "
            + ", ".join(proposal["candidates"])
            + "\n"
            + proposal["reason"]
        )
    for p in report["positions"]:
        if p["quarantined"]:
            lines.append(f"미청산 격리: {p['symbol']} {p['quantity']}주")
    if review:
        lines.append("AI 복기\n" + review[:1600])
    return "\n".join(lines)


class SlackDM:
    def __init__(self, environment, request=None):
        self.token = environment.get("SLACK_BOT_TOKEN", "")
        self.user = environment.get("SLACK_ALLOWED_USER_ID", "")
        self.request = request or self._request

    def _request(self, method, body):
        import re

        if not self.token or not re.fullmatch(r"[UW][A-Z0-9]+", self.user):
            raise ValueError("slack_dm_not_configured")
        req = urllib.request.Request(
            "https://slack.com/api/" + method,
            data=encode(body).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        # No automatic POST retry: a timeout may mean delivery succeeded.
        with open_request(req, timeout=15) as r:
            data = json.loads(r.read(100_000))
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ValueError("slack_dm_rejected")
        return data

    def send(self, key, text):
        from quantpilot.services.research_agents.publish.scrub import scrub

        cleaned = scrub(text)
        # Scrubber is presentation-only; no research output is used by the trader.
        text_value = cleaned.text if hasattr(cleaned, "text") else str(cleaned)
        self.request(
            "chat.postMessage",
            {
                # A user ID opens the bot DM through chat.postMessage itself;
                # do not require the unrelated conversations.open/im:write scope.
                "channel": self.user,
                "text": text_value[:3900],
                "client_msg_id": str(uuid5(NAMESPACE_URL, key)),
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )


def drain_outbox(store, sender):
    for row in store.db.execute(
        "SELECT * FROM outbox WHERE state='pending' ORDER BY at"
    ).fetchall():
        with store.transaction():
            claimed = store.db.execute(
                "UPDATE outbox SET state='sending' WHERE id=? AND state='pending'",
                (row["id"],),
            ).rowcount
        if not claimed:
            continue
        try:
            sender.send(row["id"], row["text"])
        except Exception as exc:
            store.db.execute(
                "UPDATE outbox SET state='delivery_unknown',error=? WHERE id=?",
                (type(exc).__name__, row["id"]),
            )
            store.audit("slack_delivery_unknown", {"id": row["id"]})
        else:
            store.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (row["id"],))


LIVENESS_GRACE_SECONDS = 180


def _queued(store, key, text, now):
    if store.db.execute("SELECT 1 FROM outbox WHERE id=?", (key,)).fetchone():
        return False
    store.enqueue(key, text, now)
    return True


def record_process_failure(store, role, result, now=None):
    """A hidden process that fails to start must leave evidence in the ledger."""
    now = now or datetime.now(timezone.utc)
    reason = result.get("reason", "unknown")
    store.audit("process_failed", {"role": role, "reason": reason}, now)
    if store.policy.slack_enabled:
        from quantpilot.paper.calendar import KST

        key = f"process_failed:{role}:{now.astimezone(KST).date().isoformat()}:{reason}"
        _queued(
            store,
            key,
            f"QuantPilot 모의운용 알림: {role} 프로세스 시작 실패 ({reason})\n"
            "원장 디렉터리의 logs 파일과 status를 확인하세요.",
            now,
        )


def check_trader_liveness(store, now, calendar=None):
    """One DM per hour while an armed trader stops heartbeating during KRX hours.

    The reporter has no exchange calendar, so weekday session hours are approximated;
    a holiday can produce a few extra reminders, a dead trader never produces none.
    """
    if store.get("control") == "stopped" or not store.policy.slack_enabled:
        return False
    from quantpilot.paper.calendar import KST

    local = now.astimezone(KST)
    if calendar is not None:
        from datetime import timedelta
        session = calendar.session(now)
        if not session or not session.opens - timedelta(minutes=10) <= now <= session.closes + timedelta(minutes=10):
            return False
    if local.weekday() >= 5 or not (
        (8, 50) <= (local.hour, local.minute) <= (15, 40)
    ):
        return False
    heartbeat = store.get("heartbeat")
    try:
        age = (now - datetime.fromisoformat(heartbeat)).total_seconds()
    except (TypeError, ValueError):
        age = None
    if age is not None and 0 <= age <= LIVENESS_GRACE_SECONDS:
        return False
    key = f"liveness:{local.date().isoformat()}:{local.hour:02d}"
    queued = _queued(
        store,
        key,
        "QuantPilot 모의운용 알림: trader heartbeat 없음"
        f" (마지막 {heartbeat or '없음'})\n"
        "보유 포지션 보호가 멈췄을 수 있습니다. 프로세스와 logs를 확인하세요.",
        now,
    )
    if queued:
        store.audit(
            "trader_liveness_alert", {"heartbeat": heartbeat, "age_seconds": age}, now
        )
    return queued


def operational_diagnostics(store, now):
    from quantpilot.paper.calendar import KST
    from quantpilot.paper.diagnostics import gate_seconds
    from quantpilot.paper.valuation import close_for_day
    from quantpilot.paper.supervisor import _status_from_store
    day = now.astimezone(KST).date().isoformat()
    quarantine = {}
    for row in store.db.execute("SELECT key,value FROM settings WHERE key LIKE 'data_quarantine:%'"):
        value = json.loads(row[1])
        if value and value.get("day") == day:
            quarantine[row[0].split(":", 1)[1]] = value
    attempts = ([dict(row) for row in store.db.execute(
        "SELECT job_id,attempt,at,state,error FROM job_attempts ORDER BY at DESC LIMIT 12")]
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_attempts'").fetchone() else [])
    return {
        "entry_gates": store.get("entry_gates", {}),
        "entry_block_seconds": gate_seconds(store, now),
        "incidents": store.get("incidents", {}),
        "feed": store.get("feed_status", {"state": "disabled"}),
        "api_budget": api_budget_diagnostics(store, now),
        "supervisor": _status_from_store(store, now) if store.policy.supervisor_enabled else {"state": "disabled"},
        "recovery_required": store.get("recovery_required", False),
        "resume_authorized_day": store.get("resume_authorized_day"),
        "close_evidence": close_for_day(store, day),
        "data_quarantine": quarantine,
        "ai_runners": store.get("ai_runners", {}),
        "ai_job_attempts": attempts,
        "last_review": store.get("last_review"),
    }


def api_budget_diagnostics(store, now):
    location = store.get("api_budget_location")
    if not location:
        return store.get("api_budget_status", {"state": "disabled"})
    import sqlite3
    from pathlib import Path
    from quantpilot.paper.calendar import KST
    try:
        db = sqlite3.connect(Path(location["path"]).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = db.execute("SELECT requests,queue_seconds,processing_seconds,retries,rate_violations FROM api_budget_daily WHERE scope=? AND day=?",
                             (location["scope"], now.astimezone(KST).date().isoformat())).fetchone()
            depth = db.execute("SELECT COUNT(*) FROM api_budget_waiters WHERE scope=? AND expires_at>?",
                               (location["scope"], now.timestamp())).fetchone()[0]
        finally:
            db.close()
        return dict(zip(("requests", "queue_seconds", "processing_seconds", "retries", "violations"), row or (0, 0, 0, 0, 0)),
                    state="enabled", queue_depth=depth, observed_at=now.isoformat())
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        return {"state": "unavailable", "violations": None}


def operation_report_once(store, calendar, now):
    """Independent close numbers. Never wait for trader or AI, never resend unknown delivery."""
    if not store.policy.independent_reports_enabled:
        return {"status": "disabled"}
    from quantpilot.paper.calendar import KST
    from quantpilot.paper.valuation import close_for_day
    session = calendar.session(now)
    if not session or now < session.closes:
        return {"status": "waiting"}
    day = now.astimezone(KST).date().isoformat()
    close = close_for_day(store, day)
    confirmed = bool(close and close.get("valid"))
    previous = store.get("operation_report:" + day)
    correction = previous is not None and not previous.get("close_confirmed") and confirmed
    if previous and not correction:
        return {"status": "already_reported"}
    report = snapshot(store, now=now)
    report.update(report_day=day, report_at=now.isoformat(), close_confirmed=confirmed,
                  close_report_delay_seconds=max(0, (now - session.closes).total_seconds()),
                  report_kind="correction" if correction else "operation")
    if confirmed:
        base = close.get("day_base", store.get("day_base", store.get("initial_capital")))
        report["equity"] = round(close["equity"], 2)
        report["cash"] = round(close.get("cash", report["cash"]), 2)
        report["positions"] = close.get("position_snapshot", report["positions"])
        report["open_orders"] = close.get("open_orders", report["open_orders"])
        report["daily_pnl"] = round(close["equity"] - base, 2)
        report["daily_return"] = close["equity"] / base - 1
        report["day_base_source"] = close.get("day_base_source", report["day_base_source"])
        report["cumulative_pnl"] = round(close["equity"] - store.get("initial_capital"), 2)
        report["cumulative_return"] = close["equity"] / store.get("initial_capital") - 1
        report["valuation_incomplete"] = False
        report["daily_pnl_incomplete"] = not close.get("day_base_valid", store.get("day_base_valid", False))
    else:
        report["valuation_incomplete"] = True
        report["daily_pnl_incomplete"] = True
    trades = [dict(r) for r in store.db.execute("SELECT * FROM trades")
              if datetime.fromisoformat(r["at"]).astimezone(KST).date().isoformat() == day]
    orders = {o["id"]: o for o in store.orders()}
    carry = []
    for trade in trades:
        order = orders.get(trade["id"].rsplit(":", 1)[0], {})
        earlier_buys = [o for o in orders.values() if o["side"] == "buy" and o["symbol"] == trade["symbol"] and o["at"] <= order.get("at", "")]
        if earlier_buys and datetime.fromisoformat(max(earlier_buys, key=lambda o: o["at"])["at"]).astimezone(KST).date().isoformat() < day:
            carry.append(trade)
    report["session_trades"] = {"closed_fill_events": len(trades), "net_pnl": sum(t["pnl"] for t in trades),
                                "carry_net_pnl": sum(t["pnl"] for t in carry),
                                "modeled_cost": sum((t["gross_pnl"] or 0) - t["pnl"] for t in trades),
                                "slippage_adjusted_pnl": sum(t["adjusted_pnl"] for t in trades)}
    key = ("correction:" if correction else "operation:") + day
    with store.transaction():
        if previous is None:
            store.put("operation_initial:" + day, report)
        store.put("operation_report:" + day, report)
        store.put("last_report", report)
        store.enqueue(key, ("종가 확인 정정\n" if correction else "운영 숫자 보고\n") + render(report)
                      + ("\n종가 대사 확인" if confirmed else "\n종가 미확인 — 확인 후 정정 보고")
                      + "\nAI 복기는 별도 보고합니다.", now)
        store.put("ai_due", {"kind": "postclose", "key": day + ":postclose"})
        store.audit("operation_report_queued", {"id": key, "close_confirmed": confirmed}, now)
    return {"status": "queued", "id": key, "close_confirmed": confirmed}


def enqueue_recovery_report(store, now):
    """Supersede unsent history atomically; never requeue ambiguous deliveries."""
    key = "recovery:" + str(store.get("valuation_invalidated_before", now.isoformat()))
    with store.transaction():
        if store.db.execute("SELECT 1 FROM outbox WHERE id=?", (key,)).fetchone():
            return key
        old = [r[0] for r in store.db.execute("SELECT id FROM outbox WHERE state='pending'")]
        store.db.execute("UPDATE outbox SET state='superseded',error='recovery_report_replaced' WHERE state='pending'")
        previous = store.get("last_report")
        if previous is not None:
            store.put("report_before_recovery:" + key, previous)
        report = snapshot(store, now=now)
        report["notification_status"] = "pending"
        store.put("last_report", report)
        store.put("last_report_superseded", False)
        store.enqueue(key, "QuantPilot 원장 복구 정정 보고\n복구 적용 시점: " + now.isoformat() + "\n" + render(report) +
                      "\n신규 진입 중지 유지. 장후 잔량은 다음 거래 가능 시간의 기존 지정가 청산 대상입니다.", now)
        store.audit("recovery_report_queued", {"id": key, "superseded_ids": old}, now)
    return key
