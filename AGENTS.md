# QuantPilot Agent Workflow

QuantPilot은 fixture-first 안전 중심 트레이딩 운영자 하네스다. 실거래는 기본적으로 비활성 상태여야 한다.

## aorch workflow

- 공통 작업 판단은 전역 adaptive-orchestrate를 따른다. 작고 명확한 작업은 현재 에이전트가 직접 처리한다.
- 독립 실행이나 문맥 분리가 유리할 때 위임하며, 계획·작업보드·고정 역할 수를 모든 작업에 요구하지 않는다.
- 최초 수신자가 범위, 제공자 선택, 통합과 완료 증거를 책임진다. 안전 중요 변경은 구현자와 다른 검토자의 승인이 필요하다.
- 기존 작업보드가 있거나 병렬 소유권을 조정할 때 [협업 어댑터](docs/agent_collaboration_protocol.md)를 적용하고 lease와 소유 경로를 지킨다.
- 프로젝트 에이전트·스킬 원본은 .agents/aorch/definitions.json과 참조 파일이다. 제품별 생성 파일의 별도 수정은 충돌이다.
- 사용자 요청 없이 commit·push·PR을 만들지 않는다. 완료된 작업보드는 역사적 증거로 보존한다.

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
- 리서치 에이전트 산출물(시황·후보 노트)은 거래 입력이 아니며 `quantpilot/services/research_agents`와
  `services/briefing`은 거래 코드를 import 할 수 없다(`tach.toml`, `test_research_agents_boundary.py`).
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

웹 클라이언트는 사용자 승인으로 2026-09-10 제거했다. 새 모의운용 검증은 `python scripts/verify-paper.py`와 `tach check`를 따른다. 프런트 npm 검사와 타입 산출물을 복원하지 않는다.

## Level 5 references

Level 5 구현 전 다음 문서를 읽는다.

- [Fable5 Level 5 implementation spec](docs/fable5_level5_implementation_spec.md)
- [Operator contracts](docs/contracts/operator_contracts.md)
- 해당 미션의 활성 작업보드
