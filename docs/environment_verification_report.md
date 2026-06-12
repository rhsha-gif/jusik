# QuantPilot Operator Environment Verification Report

Date: 2026-06-12

Workspace inspected: `C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더`

Git root detected: `C:\Users\goyan`

## 1. Repository Structure Status

The QuantPilot workspace directory is currently empty except for this report.

| Path | Status |
| --- | --- |
| `apps/web/` | Missing |
| `services/api/` | Missing |
| `packages/core/` | Missing |
| `packages/brokers/` | Missing |
| `packages/db/` | Missing |
| `jobs/` | Missing |
| `tests/` | Missing |
| `docs/` | Present, created for this report |

Recommended structure for the pre-harness step:

```text
apps/web/
services/api/
packages/core/
packages/brokers/
packages/db/
jobs/
tests/
docs/
```

## 2. Runtime Tool Status

| Tool | Status | Observed version / note |
| --- | --- | --- |
| `python` | Present | Python 3.11.15 at `C:\Users\goyan\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe` |
| `pip` | Present | Bare `pip` points to Python 3.14: `C:\Users\goyan\AppData\Local\Python\pythoncore-3.14-64\...`; use `python -m pip` for the active Python 3.11 runtime |
| `python -m pip` | Present | pip 24.0 for Python 3.11 Hermes venv |
| `node` | Present | v24.16.0 |
| `npm` | Present | 11.13.0 |
| `pnpm` | Missing | Not available on PATH |
| `docker` | Present | Docker 29.5.3 |
| `docker compose` | Present | Docker Compose v5.1.4 |
| Docker daemon | Running | `docker info` returned server version 29.5.3 |
| `make` | Missing | Not available on PATH |
| `git` | Present | git version 2.54.0.windows.1 |

## 3. Python Package Status

Checked against the active `python` runtime, not bare `pip`.

| Package | Status | Version / note |
| --- | --- | --- |
| `fastapi` | Installed | 0.133.1 |
| `uvicorn` | Installed | 0.41.0 |
| `pydantic` | Installed | 2.13.4 |
| `pydantic-settings` | Installed | 2.13.1 |
| `pytest` | Installed | 9.0.3 |
| `pytest-asyncio` | Missing | Required for async API tests |
| `httpx` | Installed | 0.28.1 |
| `pandas` | Installed | 3.0.3 |
| `numpy` | Installed | 2.4.6 |
| `pyyaml` | Installed | 6.0.3 |
| `sqlalchemy` | Missing | Required for database layer |
| `alembic` | Missing | Required for migrations |
| `redis` | Installed | 8.0.0 |
| `rq` | Installed | 2.9.1 |
| `celery` | Missing | Optional alternative to `rq`; not required if pre-harness chooses `rq` |

Optional future quant packages:

| Package | Installed | Readiness / installation risk |
| --- | --- | --- |
| `vectorbt` | No | Available on PyPI in prior check; compatibility should be pinned and tested with current NumPy/Pandas versions |
| `backtrader` | No | Available on PyPI in prior check; generally lower install risk |
| `PyPortfolioOpt` | No | Available on PyPI in prior check; may require dependency pinning around NumPy/SciPy stack |
| `qlib` / `pyqlib` | No | Available on PyPI as `pyqlib`; higher integration risk and heavier dependency surface |
| `FinRL` | No | Available on PyPI in prior check; high dependency and environment risk |
| `ta-lib` | No | Available on PyPI as `TA-Lib`; higher Windows native-library/wheel risk |
| `pandas-ta` | No | Not confirmed from active pip index check; consider alternatives or verify package name/source during later quant-library evaluation |

Optional packages are not required for the pre-harness.

## 4. Frontend Package Status

No `package.json`, lockfile, or frontend project exists in this workspace.

| Item | Status |
| --- | --- |
| Next.js project | Not present |
| `apps/web/` | Missing |
| npm lockfile | Missing |
| pnpm lockfile | Missing |

Recommended setup during pre-harness or frontend scaffold step:

- Create `apps/web/`.
- Add a Next.js app only when the pre-harness prompt explicitly asks for it.
- Prefer one package manager for the repo. Since `npm` is present and `pnpm` is missing, `npm` is the currently ready option.

## 5. Safety Configuration Status

No environment/config files currently exist in the workspace.

| File / key | Status |
| --- | --- |
| `.env.example` | Missing |
| `.env.local` | Missing |
| `.gitignore` | Missing |
| `LIVE_TRADING_ENABLED` | Not found |
| `BROKER_MODE` | Not found |
| `DEFAULT_BROKER_MODE` | Not found |
| `DEFAULT_ORDER_TYPE` | Not found |
| `MARKET_ORDERS_ENABLED` | Not found |
| KIS credential placeholders | Not found |
| KIWOOM credential placeholders | Not found |

Required safe defaults to add during pre-harness:

```text
LIVE_TRADING_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
```

Safety finding: no live broker credentials were found in this workspace scan because there are currently no files to scan. No broker endpoints were called and no order actions were attempted.

## 6. Test Command Readiness

| Command | Status |
| --- | --- |
| `make test` | Not ready; `make` and `Makefile` are missing |
| `make api` | Not ready; `make` and `Makefile` are missing |
| `make smoke` | Not ready; `make` and `Makefile` are missing |
| `pytest` | Ready as a command; no tests exist yet |
| `python -m pytest` | Ready; reports pytest 9.0.3 |

Recommended pre-harness additions:

- Add a `Makefile` only if the target Windows environment will have `make`, or provide equivalent npm/Python scripts for Windows.
- Add smoke tests that prove live trading remains disabled by default.
- Add API tests after `pytest-asyncio`, `sqlalchemy`, and `alembic` are included in the project environment.

## 7. Missing Requirements

Required for a safe pre-harness:

- Project directory structure.
- `.env.example` with safe trading defaults.
- `.gitignore` that excludes local env files and secrets.
- A project-owned Python dependency file, such as `pyproject.toml` or `requirements.txt`.
- `pytest-asyncio`.
- `sqlalchemy`.
- `alembic`.
- API/test/smoke command definitions.
- Explicit broker mock mode and live-trading guard configuration.

Environment/tooling gaps:

- `make` is missing.
- `pnpm` is missing.
- Bare `pip` targets Python 3.14 while `python` targets Python 3.11. Use `python -m pip` or create a project-local virtual environment to avoid package drift.

## 8. Recommended Next Step

Run the pre-harness implementation prompt next. The pre-harness should create the repository skeleton, safe config defaults, mock-only broker boundary, dependency declarations, and verification tests before any product feature work.

Environment verification completed.

Safe to proceed to Pre-Harness: YES

Reasons:

- The workspace is empty, so no unsafe live broker integration is currently enabled.
- Docker, Node/npm, Git, Python 3.11, and pytest are available.
- Required project safety defaults are missing, but they can be created in the pre-harness step before any feature implementation.
- Missing Python packages and command wrappers are ordinary scaffold tasks, not blockers to starting pre-harness.

Next command or next prompt:

- Use `02_CODEX_PRE_HARNESS_IMPLEMENTATION_PROMPT.md`
