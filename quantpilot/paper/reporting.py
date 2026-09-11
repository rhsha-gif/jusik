"""Code-computed PnL and a durable, at-most-once Slack DM outbox."""

from __future__ import annotations
from datetime import datetime, timezone
import json
import urllib.request
from quantpilot.paper.http import open_request
from uuid import uuid5, NAMESPACE_URL

from quantpilot.paper.store import encode


def snapshot(store):
    from quantpilot.paper.recommendations import propose_concentration

    marks = store.get("marks", {})
    positions = store.positions()
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
        "universe_source": store.get("universe_source"),
        "flatten_pending": store.get("control") == "flattening",
        "unverified_symbols": store.get("unverified_symbols", []),
        "candidate_status": {
            s: store.get("candidate_status:" + s, "not_evaluated")
            for s in store.get("universe", [])
        },
        "valuation_incomplete": any(p["symbol"] not in marks for p in positions),
        "cost_basis": "modeled_fee_tax; slippage_separate",
        "live_trading_enabled": False,
    }


def render(report, review=""):
    lines = [
        f"QuantPilot {report['data_mode']} 일일 보고",
        f"당일 {report['daily_return']:+.2%} / {report['daily_pnl']:+,.0f}원",
        f"누적 {report['cumulative_return']:+.2%} / {report['cumulative_pnl']:+,.0f}원",
        f"평가자산 {report['equity']:,.0f}원 · 현금 {report['cash']:,.0f}원",
        "손익은 체결금액에 설정된 수수료·세금 가정을 반영합니다.",
    ]
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
            "일부 보유분은 최신 평가 미확인: 표시 자산에 취득원가 평가가 포함됩니다."
        )
    loss = report.get("intraday_loss_state")
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
