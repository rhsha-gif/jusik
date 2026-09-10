"""Explain a possible simplification; never change strategy or allocation policy."""


def propose_concentration(store):
    evidence = []
    for strategy in store.policy.active_strategies:
        row = store.db.execute(
            """SELECT COUNT(DISTINCT o.id),COUNT(DISTINCT substr(t.at,1,10)),
            COALESCE(SUM(t.adjusted_pnl),0) FROM trades t JOIN orders o
            ON o.id=substr(t.id,1,instr(t.id,':')-1)
            WHERE t.strategy=? AND o.state='filled'""",
            (strategy,),
        ).fetchone()
        evidence.append(
            {
                "strategy": strategy,
                "closed_orders": row[0],
                "days": row[1],
                "net": row[2],
            }
        )
    if len(evidence) < 2 or any(
        r["closed_orders"] < 20 or r["days"] < 5 for r in evidence
    ):
        return None
    positive = [r for r in evidence if r["net"] > 0]
    if 0 < len(positive) < len(evidence):
        return {
            "kind": "consider_fewer_strategies",
            "candidates": [r["strategy"] for r in positive],
            "evidence": evidence,
            "automatic_change": False,
            "reason": "각 전략의 최소 관측량을 확보한 뒤 비용 보정 손익이 양수인 전략으로 시험을 좁히는 방안을 제안합니다. 향후 우위를 확정한 판단은 아닙니다.",
        }
    return None
