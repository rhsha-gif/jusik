# QuantPilot Codex Adapter

Codex는 [Best-Fit 협업 프로토콜](../docs/agent_collaboration_protocol.md)을 따른다. 새 작업은
[미션 작업보드 템플릿](../docs/agent_workboard_template.md)과
[능력 점수표](../docs/agent_capability_scorecard.md)로 라우팅한다.

## Mission behavior

- Codex가 최초 요청을 받으면 미션 리드를 유지하되 적합도가 높은 Claude 작업을 자동으로 분리한다.
- Claude 작업이 `ready`가 되면 사용자에게 `🔔 Claude Code/Fable5 작업 시점`을 알리고 안전 범위에서는
  승인 대기 없이 실행한다.
- Codex의 초기 우선 강점은 저장소 탐색, DB·API·UI·job 통합, 실행 기반 디버깅, 빌드·테스트 루프와
  mainline 통합이다. 이 목록은 고정 소유권이 아니다.
- `codex/<mission-id>-<task-id>` branch의 전용 worktree에서 작업하고 자기 경로만 commit한다.
- 미션 리드인 경우에만 상대 commit을 검토하고 mainline에 통합한다.
- 같은 원인의 실패가 두 번 반복되면 수정을 멈추고 Claude의 읽기 전용 진단을 받는다.

## QuantPilot safety and checks

다음 기본값을 변경하지 않는다.

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

Frontend 변경 시 `quantpilot/apps/web`에서 `npm run test`와 `npm run build`를 모두 실행한다.
