---
name: workboard-flow
description: "QuantPilot 미션 작업보드(docs/*_workboard.md)의 태스크 상태를 프로토콜대로 바꾸는 절차: 문서 편집 lease 획득·해제, ready 작업 claim(정확히 하나), ready→in_progress→review→integrated→done/blocked 상태 전이, checkpoint log 기록, 커밋 해시와 정확한 검증 수치(pytest junit, smoke)가 담긴 handoff record(인계 기록) 작성. QP-KER-020, QP-DRIFT-002 같은 태스크 ID를 claim해라/시작해라/리뷰로 옮겨라/integrated로 바꿔라/blocked 처리해라는 요청, \"작업보드 갱신\", \"workboard에 기록\", \"핸드오프 기록 남겨줘\", \"인계 준비해줘\", \"체크포인트 남겨줘\", \"lease 잡고 상태 바꿔\" 등 기존 미션 작업보드의 상태·기록을 변경하는 모든 작업에 반드시 이 스킬을 사용한다. 구현이 끝나서 작업보드에 증거와 함께 review 전환하는 경우도 포함한다. Use for ANY status transition, claim, checkpoint, or handoff on an EXISTING mission workboard (task IDs like QP-XXX-NNN). Not for creating a brand-new mission workboard (that is /start-collaboration) and not for writing a Codex implementation task spec (that is the codex handoff skill)."
---

# workboard-flow

QuantPilot 미션 작업보드(docs/*_workboard.md)의 태스크 상태를 프로토콜대로 바꾸는 절차: 문서 편집 lease 획득·해제, ready 작업 claim(정확히 하나), ready→in_progress→review→integrated→done/blocked 상태 전이, checkpoint log 기록, 커밋 해시와 정확한 검증 수치(pytest junit, smoke)가 담긴 handoff record(인계 기록) 작성. QP-KER-020, QP-DRIFT-002 같은 태스크 ID를 claim해라/시작해라/리뷰로 옮겨라/integrated로 바꿔라/blocked 처리해라는 요청, "작업보드 갱신", "workboard에 기록", "핸드오프 기록 남겨줘", "인계 준비해줘", "체크포인트 남겨줘", "lease 잡고 상태 바꿔" 등 기존 미션 작업보드의 상태·기록을 변경하는 모든 작업에 반드시 이 스킬을 사용한다. 구현이 끝나서 작업보드에 증거와 함께 review 전환하는 경우도 포함한다. Use for ANY status transition, claim, checkpoint, or handoff on an EXISTING mission workboard (task IDs like QP-XXX-NNN). Not for creating a brand-new mission workboard (that is /start-collaboration) and not for writing a Codex implementation task spec (that is the codex handoff skill).

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [workboard-flow] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:workboard-flow; mode=bridge; edit .agents/aorch/definitions.json -->
