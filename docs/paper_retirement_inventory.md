# 구형 기능 정리 목록 — 승인된 59개 삭제 완료

새 모의운용 경로는 `python -m quantpilot.paper`다. 2026-09-10 사용자 승인 후 아래 웹 파일 59개를 삭제했다. node_modules·빌드 산출물·목록 밖 파일은 보존했다.

| 구형 기능 | 새 경로 | 처리 |
|---|---|---|
| 웹 overview/operator/execution | CLI status/pause/resume/flatten/report | 웹 제거 완료 |
| 웹 설정·전략 화면 | CLI config/strategies | 웹 제거 완료 |
| 웹 장전 브리핑·연구 | AI worker의 preopen/hourly/postclose | 기존 연구 기록 보존 |
| `jobs/run_kis_paper_session.py`의 Level 승격·승인 | paper/runtime → 기존 durable submission | 새 경로에서 미호출, 기존 진입점은 호환성 검토 후 별도 폐기 |
| `jobs/prepare_kis_paper_runtime.py` | 새 독립 원장·versioned policy | 기존 연결 작업과 사용자 변경을 보존 |
| 기존 core/execution·db 저널 | paper/broker에서 직접 재사용 | 삭제 금지 |

웹 삭제에 따른 갱신 대상: `docs/STATUS.md`, `docs/roadmap_acceptance_matrix.md`, AGENTS의 frontend 검증 지침을 만드는 aorch 원본. `docs/_archive`의 과거 완료 증거·기록은 수정하거나 삭제하지 않는다. 기존 API 서버와 스키마는 CLI 주문 커널과의 의존성을 확인하기 전까지 보존한다.

아래 파일 목록은 `rg --files quantpilot/apps/web`로 수집했다. node_modules·빌드 산출물 등 무시된 경로는 삭제 승인 범위에 포함하지 않는다.

삭제 후 Git 삭제 집합과 승인 목록의 59개 경로가 정확히 일치함을 확인했다. 전체 테스트 1,476 passed·2 skipped, smoke(mock 체결 3건·실거래 false), tach, diff 검사를 통과했다. aorch의 openapi-sync·status-sync 원본을 수정하고 양쪽 제공자 산출물 동기화를 확인했다. 실제 모의주문과 Slack 전송은 실행하지 않았다.

## 웹 파일 목록
- quantpilot/apps/web\.env.example
- quantpilot/apps/web\index.html
- quantpilot/apps/web\package-lock.json
- quantpilot/apps/web\package.json
- quantpilot/apps/web\public\favicon.svg
- quantpilot/apps/web\src\App.tsx
- quantpilot/apps/web\src\components\charts.tsx
- quantpilot/apps/web\src\components\json-viewer.tsx
- quantpilot/apps/web\src\components\page-header.tsx
- quantpilot/apps/web\src\components\shell\app-shell.tsx
- quantpilot/apps/web\src\components\shell\health-pill.tsx
- quantpilot/apps/web\src\components\shell\safety-banner.tsx
- quantpilot/apps/web\src\components\shell\theme-toggle.tsx
- quantpilot/apps/web\src\components\states.tsx
- quantpilot/apps/web\src\components\ui\badge.tsx
- quantpilot/apps/web\src\components\ui\button.tsx
- quantpilot/apps/web\src\components\ui\card.tsx
- quantpilot/apps/web\src\components\ui\dialog.tsx
- quantpilot/apps/web\src\components\ui\input.tsx
- quantpilot/apps/web\src\components\ui\misc.tsx
- quantpilot/apps/web\src\components\ui\select.tsx
- quantpilot/apps/web\src\components\ui\stat.tsx
- quantpilot/apps/web\src\components\ui\tabs.tsx
- quantpilot/apps/web\src\index.css
- quantpilot/apps/web\src\lib\activity-log.ts
- quantpilot/apps/web\src\lib\api.ts
- quantpilot/apps/web\src\lib\browser-notifications.ts
- quantpilot/apps/web\src\lib\openapi.d.ts
- quantpilot/apps/web\src\lib\queries.ts
- quantpilot/apps/web\src\lib\theme.ts
- quantpilot/apps/web\src\lib\types.ts
- quantpilot/apps/web\src\lib\utils.ts
- quantpilot/apps/web\src\lib\working-policy.ts
- quantpilot/apps/web\src\main.tsx
- quantpilot/apps/web\src\pages\briefing.tsx
- quantpilot/apps/web\src\pages\execution.tsx
- quantpilot/apps/web\src\pages\jobs.tsx
- quantpilot/apps/web\src\pages\operator.tsx
- quantpilot/apps/web\src\pages\overview.tsx
- quantpilot/apps/web\src\pages\policies.tsx
- quantpilot/apps/web\src\pages\research.tsx
- quantpilot/apps/web\src\pages\run.tsx
- quantpilot/apps/web\src\pages\settings.tsx
- quantpilot/apps/web\src\pages\signals.tsx
- quantpilot/apps/web\src\pages\studio.tsx
- quantpilot/apps/web\src\test\api.test.ts
- quantpilot/apps/web\src\test\app-routing.test.tsx
- quantpilot/apps/web\src\test\browser-notifications.test.ts
- quantpilot/apps/web\src\test\execution-page.test.tsx
- quantpilot/apps/web\src\test\operator.test.tsx
- quantpilot/apps/web\src\test\run-mock-execute.test.tsx
- quantpilot/apps/web\src\test\safety-banner.test.tsx
- quantpilot/apps/web\src\test\setup.ts
- quantpilot/apps/web\src\test\working-policy.test.ts
- quantpilot/apps/web\src\vite-env.d.ts
- quantpilot/apps/web\tsconfig.app.json
- quantpilot/apps/web\tsconfig.json
- quantpilot/apps/web\tsconfig.node.json
- quantpilot/apps/web\vite.config.ts
