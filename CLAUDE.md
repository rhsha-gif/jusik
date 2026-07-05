# QuantPilot Fable5 Level 5 Handoff

QuantPilot is a safe, fixture-first portfolio operator harness. Fable5 may implement Level 5 only inside the contracts, flags, and tests prepared for this repository.

## Read First

- `AGENTS.md`
- `README.md`
- `docs/fable5_level5_implementation_spec.md`
- `docs/contracts/operator_contracts.md`
- `docs/level_3_4_implementation_report.md`
- `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`

## Commands

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

Use `make test` and `make smoke` only where `make` is available.

## Frontend & Local Servers (quantpilot/apps/web)

- Dev server: `npm run dev` (vite, port 5173 — 다른 세션과 충돌 시 죽이지 말고 `--port 5174` 사용)
- Test / build: `npm run test` (vitest), `npm run build` — 프론트 변경 시 둘 다 필수
- API 서버: `python -m uvicorn quantpilot.services.api.main:app --port 8010` (충돌 시 8011, 8012)
- 헬스체크: `curl -s http://127.0.0.1:8010/api/health`
- `openapi.json` 변경 시 apps/web에서 `npm run generate:api`로 타입 재생성

## User Conventions (learned from past sessions)

- 사용자 보고·요약은 한국어, 코드·커밋 메시지는 영어.
- 스테이지(단계) 범위를 벗어나는 기능 추가 금지 — 사용자가 명시적으로 범위를 제한한 이력 있음 (예: "KIS websocket, realtime wiring은 이 스테이지에 넣지 말 것").
- 완료 보고 형식: pytest + run_smoke 실제 출력 근거 제시.
- 프론트엔드 디자인 작업 시 `design.md`를 먼저 읽을 것.

## Safety Invariants

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- Level 5 tests must not call live brokers or require secrets.
- Fully automated runs must be blocked unless all feature flags, policy gates, fallback checks, and risk gates pass.

## Forbidden Actions

- Do not add live broker credentials or read secret files.
- Do not enable live trading defaults.
- Do not bypass the order state machine or risk gates.
- Do not emit raw broker orders from LLM/RL outputs.
- Do not broaden refactors outside Level 5 surfaces.

## Working Rules

- Preserve existing user changes in the worktree.
- Ground progress claims in actual command output.
- Keep diffs narrow and consistent with existing Pydantic/FastAPI patterns.
- Use subagents for risk review and test review when available.
- Treat pending Level 5 tests as implementation targets, not proof that Level 5 exists.
