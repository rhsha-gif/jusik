# QuantPilot Agent Workflow

QuantPilot은 fixture-first 안전 중심 트레이딩 운영자 하네스다. 실거래는 기본적으로 비활성 상태여야 한다.

## Best-Fit Codex–Claude collaboration

- 모든 비단순 작업은 [범용 협업 프로토콜](docs/agent_collaboration_protocol.md)을 따른다.
- 새 미션은 [작업보드 템플릿](docs/agent_workboard_template.md)을 복사해 목표, 작업 그래프, 라우팅 점수,
  worktree, 소유 경로, 검증 조건을 먼저 확정한다.
- 담당자 선정에는 [능력 점수표](docs/agent_capability_scorecard.md)의 모델별 실적을 사용한다.
- 최초 임무 수신자가 미션 리드를 유지한다. Codex와 Claude Code의 고정 직군은 없으며 작업별 적합도가
  높은 쪽이 구현을 소유한다.
- 상대 에이전트의 초기 분해 검토와 최소 하나의 검증 가능한 실질 산출물은 필수다.
- 각 에이전트는 별도 worktree와 `codex/<mission>-<task>` 또는 `claude/<mission>-<task>` 브랜치에서
  자기 경로만 수정하고 직접 커밋한다.
- 미션 리드만 mainline을 통합하고 상대 커밋 검토와 프로젝트 전체 검증을 책임진다.
- 완료된 `docs/professional_operator_workboard.md`는 역사적 증거다. 새 미션 상태를 그 문서에 추가하지 않는다.

## QuantPilot safety adapter

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- broker 자격증명, API key, 계좌 ID, 비밀 또는 개인 거래 정보를 저장소에 추가하지 않는다.
- 외부 connector는 fake-client unit test와 skipped/manual integration test만 사용한다.
- unit test는 인터넷이나 비밀을 요구하지 않아야 하며 fixture 결정성을 보존한다.
- data mode는 `fixture`, `local_historical`, `external_historical`, `realtime_market_data`,
  `paper_trading`, `live_trading_candidate`, `live_canary`, `live_scaled` 중 하나로 명시한다.
- 거래 관련 변경은 pre-trade risk check, kill switch, idempotency, order state machine, audit logging,
  reconciliation을 우회할 수 없다.
- LLM/RL 출력은 broker 주문을 직접 생성, 승인 또는 제출할 수 없다.
- 실패한 안전 테스트를 약화하지 말고 원인을 수정한다.
- 기존 사용자 변경을 덮어쓰거나 작업 커밋에 포함하지 않는다.

## Required verification

Backend 변경 후:

```powershell
python -m pytest quantpilot/tests
```

Smoke 또는 orchestration 변경 후:

```powershell
python -m quantpilot.jobs.run_smoke
```

`quantpilot/apps/web` frontend 변경 후 해당 디렉터리에서:

```powershell
npm run test
npm run build
```

## Level 5 references

Level 5 구현 전 다음 문서를 읽는다.

- [Fable5 Level 5 implementation spec](docs/fable5_level5_implementation_spec.md)
- [Operator contracts](docs/contracts/operator_contracts.md)
- 해당 미션의 활성 작업보드
