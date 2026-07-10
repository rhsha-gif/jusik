# /start-collaboration

새 미션을 Best-Fit Codex–Claude 프로토콜로 접수한다.

## Procedure

1. `AGENTS.md`, `docs/agent_collaboration_protocol.md`, `docs/agent_capability_scorecard.md`를 읽는다.
2. `docs/agent_workboard_template.md`를 새 미션 작업보드로 복사한다.
3. 목표, 범위, 제외 범위, 안전 제약, 관찰 가능한 완료 조건을 기록한다.
4. 구현, 연구, 테스트, 감사 단위로 작업 그래프를 만든다.
5. 각 작업에 대해 Codex와 Claude의 5개 적합도 점수와 근거를 기록한다.
6. 더 높은 점수의 구현자와 다른 검토자를 지정한다. 0.5 이내 동점은 최초 수신자 측이 맡는다.
7. 실적 표본이 없는 적격 상대에게 low/medium-risk bounded seed 작업을 줄 수 있는지 평가한다.
8. 각 에이전트에 최소 하나의 실질 역할과 겹치지 않는 경로를 배정한다.
9. 전용 worktree/branch와 통합 순서를 기록하고 첫 작업을 `ready`로 바꾼다.
10. 상대 작업 시점을 사용자에게 알린 뒤 안전 범위에서는 자동 실행한다.

프로토콜 접수 자체가 live trading, secret access 또는 외부 상태 변경 권한을 부여하지 않는다.
