# QuantPilot Claude Code Adapter

Claude Code는 AGENTS.md의 안전 규칙과 aorch 공통 워크플로를 따른다. 관련 도메인 지침과 현재 작업에 필요한 정의만 읽는다.

## Mission behavior

- 최초 수신자가 리드로서 범위, 라우팅, 통합과 완료 증거를 책임진다. 작은 작업은 직접 처리한다.
- 위임은 독립 작업이나 문맥 분리가 유리할 때 선택한다. 상대 제공자 호출, 점수표, 작업보드를 항상 만들지 않는다.
- 활성 작업보드가 있으면 workboard-flow의 lease·claim·소유 경로·인계 규칙을 지킨다.
- 사용자 요청 없이 commit·push·PR을 만들지 않는다. 기존 사용자 변경을 수정·stash·reset·정리하거나 커밋에 포함하지 않는다.
- 같은 원인의 실패가 반복되면 증거를 재검토하고 독립 진단이 유익한지 판단한다. 안전 중요 변경은 별도 검토자 승인 없이 완료하지 않는다.
- 질문·승인이 필요한 위임 작업은 blocked.inputRequest로 부모 대화에 반환한다. 입력 대기를 실패나 승인으로 처리하지 않는다.
- 프로젝트 원본은 .agents/aorch/definitions.json이다. 필수 native 기능이 없으면 실행 전에 지원 제공자로 라우팅한다.

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

This project's commands are written for PowerShell (below); PowerShell is the working convention here and overrides the user-level Bash default.

Use an interpreter with the project dependencies installed (`pytest`, `fastapi`, and `pydantic`). On the current machine, `python` resolves to the hermes-agent venv; a project venv is the intended home for these dependencies. The command must run with an interpreter that has all three packages.

```powershell
# A user-supplied basetemp is recursively deleted by pytest, so it must be
# unique per run. The shared Temp/pytest-of-goyan directory is not writable on
# this machine.
python -m pytest quantpilot/tests --basetemp ".pytest_tmp/$PID"
python -m quantpilot.jobs.run_smoke
```

웹 클라이언트는 사용자 승인으로 2026-09-10 제거했다. 새 모의운용 검증은 `python scripts/verify-paper.py`와 `tach check`를 따른다. 프런트 npm 검사와 타입 산출물을 복원하지 않는다.

로컬 서버 충돌 시 기존 프로세스를 종료하지 말고 다음 빈 포트를 사용한다.

## Specialized workflows

- `/start-collaboration`: 사용자가 새 협업 미션 또는 작업보드를 요청할 때 사용하는 선택적 진입점이다.
- `/write-codex-handoff`: 라우팅 결과 Codex 구현이 선택된 recipe 작업에만 사용하는 특수 명령이다.
- 기존 quant recipe, risk matrix, backtest forensics skills는 관련 작업에서 필요한 지침만 로드한다.
- `/vault-consult`: 지식 vault(`quantpilot-foundation/`) 조회와 인용 규약. 퀀트·리스크·집행·
  시계열·데이터/신뢰성 판단은 볼트를 근거로 삼고 `[[노트명]]`으로 인용한다. 볼트 밖 지식으로
  답할 때는 그 사실을 밝힌다.
- 리서치 에이전트(시황 팀 3·투자 팀 4, `qp-*`)와 잡 `run_market_brief`·`run_invest_research`:
  `docs/research_agents.md`. 산출물은 읽기 전용이며 거래 입력이 아니다.
- `/ship` 보안 게이트: 스테이징 diff가 `qp-security-gate`의 `ship_triggers`에 걸리면
  `scripts/security-gate.ps1 -Staged` 후 에이전트 판정 없이는 커밋하지 않는다(`docs/security_gate.md`).

사용자 보고는 한국어, 코드와 commit message는 영어를 기본으로 한다.
