# QuantPilot Claude Code Adapter

Claude Code는 Codex와 같은 [Best-Fit 협업 프로토콜](docs/agent_collaboration_protocol.md)을 따른다.
작업 시작 전에 `AGENTS.md`, 활성 미션 작업보드, [능력 점수표](docs/agent_capability_scorecard.md), 관련
도메인 문서를 읽는다.

## Mission behavior

- Claude가 최초 요청을 받으면 미션 리드가 되어 계획, 라우팅, 통합, 완료 보고를 책임진다.
- Codex가 리드인 경우에도 점수가 높은 Claude 작업은 연구에 한정하지 않고 전체 기능 구현과 테스트를
  소유할 수 있다.
- 초기 우선 강점은 연구 종합, 퀀트·리스크 설계, 장문 계약, 알고리즘 구현, 적대 테스트와 독립 감사다.
  이는 우선순위일 뿐 고정 경계가 아니다.
- 구현 전 상대의 작업 분해 검토를 받고, 모든 비단순 미션에서 상대에게 실질 역할을 부여한다.
- 작업보드에서 정확히 하나의 `ready` 작업을 claim한 뒤 `in_progress`로 바꾼다.
- 완료된 `docs/professional_operator_workboard.md`의 Codex 전용 소유권 규칙은 역사 기록이며 새 미션에는
  적용하지 않는다.
- `claude/<mission-id>-<task-id>` branch의 분리 worktree에서 작업하고 기록된 소유 경로만 수정한다.
- 자기 branch에서는 자기 파일을 stage하고 commit할 수 있다. 다른 branch, mainline, 사용자 dirty 경로는
  수정, 정리, stash, reset 또는 commit하지 않는다.
- Claude가 미션 리드일 때만 검토된 상대 커밋을 mainline에 통합한다.
- 인계에는 commit hash, 소유 경로, 정확한 검증 출력, 알려진 제한, 통합 요청을 포함한다.

## Failure and review

- 같은 원인의 실패가 두 번 반복되면 추가 수정을 멈추고 Codex의 읽기 전용 진단을 요청한다.
- 안전 중요 변경은 Codex 또는 별도의 검토자가 승인해야 하며 Claude가 자기 변경을 최종 승인하지 않는다.
- 설계 충돌은 근거와 최소 실험을 비교한 뒤 미션 리드가 결정한다.
- 범위 확대, 외부 상태 변경, 비밀 또는 거래 권한 변경에만 사용자 승인을 요청한다.

## QuantPilot safety

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- 비밀 파일, token, credential, 실제 계좌 또는 live broker를 읽거나 추가하지 않는다.
- risk gate, kill switch, idempotency, order state machine, audit, reconciliation을 우회하지 않는다.
- 외부 connector test는 fake/offline이어야 하고 실제 KIS paper 검사는 명시적 manual opt-in으로 유지한다.

## Commands

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

Frontend 변경 시 `quantpilot/apps/web`에서:

```powershell
npm run test
npm run build
```

로컬 서버 충돌 시 기존 프로세스를 종료하지 말고 다음 빈 포트를 사용한다.

## Specialized workflows

- `/start-collaboration`: 새 미션의 기본 진입점. 작업보드와 적합도 라우팅을 만든다.
- `/write-codex-handoff`: 라우팅 결과 Codex 구현이 선택된 recipe 작업에만 사용하는 특수 명령이다.
- 기존 quant recipe, risk matrix, backtest forensics skills는 적합도 점수가 높은 작업에서 계속 사용한다.

사용자 보고는 한국어, 코드와 commit message는 영어를 기본으로 한다.
