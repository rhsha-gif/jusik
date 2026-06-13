# QuantPilot Operator Frontend Design Specification

> Place this file at the repository root as `design.md`. Fable5/Claude must treat this file as the product and visual source of truth before writing frontend code.

## 0. Product Reality Check

QuantPilot Operator Pre-Harness is **not a live brokerage trading app**. It is a safe, fixture-only operating harness for a QuantPilot workflow. The backend currently runs as a FastAPI service and exposes a Swagger/OpenAPI surface such as:

- `GET /api/health`
- `POST /api/harness/run-smoke`
- `POST /api/policies/preview`
- `POST /api/policies/parse`
- `POST /api/policies/confirm`
- `POST /api/level-1-2/run`
- `POST /api/research/universe`
- `POST /api/research/analyst`
- `POST /api/signals/board`

The frontend must never imply that real broker orders are available. The UI is a **mission-control dashboard for safe research, policy validation, signal review, and mock execution**.

Safe defaults that must be visible in the interface:

```txt
LIVE_TRADING_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
```

The core product promise:

> “A calm, modern operator cockpit that lets a user safely move from research → policy → signal → mock run → review, without ever confusing mock output for live trading.”

## 1. Design North Star

### 1.1 Emotional tone

QuantPilot should feel like:

- A premium Apple-inspired control surface: spacious, quiet, deliberate, confident.
- A Bloomberg/OpenBB/Grafana-inspired analytics workspace: information-dense, but not visually noisy.
- A safety-critical operator tool: every irreversible or risky action is clearly bounded, labeled, and reversible where possible.

Avoid the common “AI-generated dashboard” look: generic purple gradients, random glassmorphism, stock-photo hero sections, meaningless KPI cards, excessive neon, and pages that look impressive but do not guide decisions.

### 1.2 Design keywords

Use these as the aesthetic compass:

- **Calm precision**
- **Soft technical luxury**
- **Safety-first clarity**
- **High signal-to-noise**
- **Apple-like whitespace**
- **Readable financial density**
- **Operator confidence**

### 1.3 Visual metaphor

The app is not a retail trading terminal. It is a **pre-flight cockpit** for a trading system.

The user should always know:

1. Is the backend healthy?
2. Am I in mock mode?
3. What stage of the workflow am I in?
4. What data or policy produced this signal?
5. What action is safe to run next?
6. What happened after I ran it?

## 2. Users and Jobs to Be Done

### 2.1 Primary user

A developer-operator or quant researcher who has a local backend running and wants a good visual interface instead of Swagger.

### 2.2 Secondary user

A non-expert user who wants to understand the system status, mock signals, and research outputs without touching API JSON.

### 2.3 Jobs

- “Check whether the QuantPilot harness is alive.”
- “Run the smoke test and see a readable result.”
- “Create or preview a policy safely.”
- “Parse policy text/YAML into structured configuration.”
- “Confirm a policy only after seeing clear validation and risks.”
- “Generate a research universe.”
- “Request analyst-style research reports.”
- “Open a signal board and understand why each signal exists.”
- “Run level 1-2 mock workflow and review output.”
- “Export/copy JSON only when needed.”

## 3. Information Architecture

Use a single-page app with persistent shell navigation.

### 3.1 App shell

Desktop layout:

- Left sidebar, 248px wide.
- Top command/status bar, 64px high.
- Main content canvas with max-width rules per page.
- Right-side detail drawer for selected signals/jobs/policies.
- Mobile/tablet: sidebar collapses into command menu or bottom nav.

Global shell elements:

- Product mark: `QuantPilot`
- Subtitle badge: `Pre-Harness`
- Safety badge: `Mock Broker Active`
- Backend health pill: `Healthy`, `Degraded`, `Offline`
- API base selector: default `http://127.0.0.1:8010`
- Quick actions: `Run Smoke`, `Refresh`, `Open API Docs`
- Theme toggle: System / Light / Dark

### 3.2 Navigation

Recommended nav order:

1. **Overview**
2. **Research**
3. **Policies**
4. **Signal Board**
5. **Level 1-2 Run**
6. **Jobs & Logs**
7. **Settings**

### 3.3 Route map

Use React Router or a lightweight router.

```txt
/                         Overview
/research                 Research Universe + Analyst Reports
/policies                 Policy Studio
/signals                  Signal Board
/run                      Level 1-2 Run
/jobs                     Jobs & Logs
/settings                 Settings & Safety
```

## 4. Page Specifications

### 4.1 Overview

Purpose: establish trust and orientation in under 5 seconds.

Must show:

- Hero status card:
  - “QuantPilot Operator Pre-Harness”
  - “Safe mock environment. Live broker trading is disabled.”
  - Backend status from `GET /api/health`
- Safety defaults card:
  - Live trading disabled
  - Broker mode mock
  - Default order type limit
  - Market orders disabled
- Smoke test card:
  - CTA: `Run Smoke Test`
  - Last run status
  - Duration
  - Result details in collapsible JSON viewer
- Workflow cards:
  - Research Universe
  - Policy Studio
  - Signal Board
  - Level 1-2 Run

Empty/offline state:

- “Backend is not reachable at {API_BASE_URL}.”
- Show commands:
  - `python -m uvicorn quantpilot.services.api.main:app --reload --port 8010`
  - frontend dev command
- Offer retry button.

### 4.2 Research

Purpose: help the user generate or inspect stock/research inputs without dealing with raw JSON first.

Two tabs:

#### Universe tab

- Form panel:
  - Market / region selector
  - Universe seed text area
  - Risk preference selector
  - Max symbols
  - Include/exclude tags
- Result panel:
  - Table of symbols or assets
  - Confidence/eligibility badge
  - Reasons column
  - Copy JSON button
  - Save as working universe button, if backend supports it

#### Analyst Reports tab

- Input:
  - Symbol/ticker
  - Thesis/question
  - Time horizon
  - Risk focus
- Output:
  - Summary
  - Key drivers
  - Risks
  - Data caveats
  - Raw response drawer

Never fabricate market prices unless the backend returns them. If values are fixture/mock values, label them as fixture data.

### 4.3 Policy Studio

Purpose: make policy creation safe and understandable.

Layout:

- Left: policy editor
- Center: preview/validation result
- Right: safety checklist

Tabs or stepper:

1. **Draft**
2. **Parse**
3. **Preview**
4. **Confirm**

Controls:

- Policy input: YAML or plain text editor.
- Buttons:
  - `Parse Policy`
  - `Preview Policy`
  - `Confirm Policy`
- Confirm button must be disabled until parse + preview succeed.

Safety checklist:

- Uses mock broker
- Market orders disabled
- Limit orders only
- No live credentials requested
- No broker API key stored

Confirm modal copy:

> “This confirms a policy for the local pre-harness only. It does not place live broker orders.”

### 4.4 Signal Board

Purpose: convert raw signal output into an interpretable operator board.

Must include:

- Signal cards/table:
  - Symbol
  - Direction: Long / Short / Watch / Avoid / Neutral
  - Confidence
  - Source policy
  - Rationale summary
  - Risk flags
  - Timestamp or job id
- Filters:
  - Direction
  - Confidence
  - Risk flag
  - Search symbol
- Detail drawer:
  - Full rationale
  - Related policy
  - Raw JSON
  - “Copy as Markdown” and “Copy JSON”

Use color carefully:

- Blue/graphite for neutral states.
- Green only for positive/allowed/safe states.
- Amber for caution.
- Red only for errors, blocks, or high-risk warnings.
- Do not make red/green the only differentiator; include labels and icons.

### 4.5 Level 1-2 Run

Purpose: run the local mock pipeline with clear guardrails.

Screen structure:

- Pre-flight panel:
  - Backend healthy?
  - Policy selected?
  - Universe selected?
  - Mock mode active?
- Run parameters form:
  - Inputs inferred from OpenAPI schema
  - Use schema-driven forms where possible
- Run button:
  - Label: `Run Mock Level 1-2`
  - Never say `Trade Now` or `Execute Live`
- Result timeline:
  - Submitted
  - Validated
  - Research
  - Signals
  - Mock orders
  - Complete / Failed
- Result summary:
  - mock actions
  - blocked actions
  - warnings
  - raw JSON drawer

### 4.6 Jobs & Logs

Purpose: make async/job responses legible.

Features:

- Job id search
- Status chips
- Timestamp
- Duration
- Error surface
- Raw response viewer
- “Copy troubleshooting bundle” button:
  - endpoint
  - request id/job id
  - response status
  - error body

### 4.7 Settings & Safety

Must include:

- API base URL input
- Connection test button
- Current backend health
- Safe defaults display
- Feature flags display, read-only unless backend supports editing
- Theme preference
- Reset local UI state

Do not collect real broker credentials unless the backend has a verified live-trading implementation and the README is updated to state this. In the current pre-harness, credentials are out of scope.

## 5. Visual System

### 5.1 General aesthetic

Apple-inspired, not Apple-copied.

- Large quiet surfaces.
- High-quality spacing.
- Minimal borders.
- Subtle material layers.
- Typography carries hierarchy more than decoration.
- Motion is soft and purposeful.
- Pages should look expensive but not ornamental.

### 5.2 Layout grid

Desktop:

```txt
App width: fluid
Sidebar: 248px
Top bar: 64px
Page horizontal padding: 32px to 48px
Card radius: 24px
Card padding: 24px
Dense table row height: 48px
Hero section height: 220px to 320px
```

Breakpoints:

```txt
sm: 640px
md: 768px
lg: 1024px
xl: 1280px
2xl: 1536px
```

### 5.3 Color tokens

Use CSS variables and Tailwind theme tokens.

Light theme:

```css
--qp-bg: #f5f5f7;
--qp-surface: rgba(255,255,255,0.78);
--qp-surface-solid: #ffffff;
--qp-ink: #1d1d1f;
--qp-muted: #6e6e73;
--qp-hairline: rgba(0,0,0,0.10);
--qp-accent: #0a84ff;
--qp-accent-soft: rgba(10,132,255,0.12);
--qp-safe: #168a45;
--qp-warn: #b76e00;
--qp-danger: #c62828;
--qp-shadow: 0 18px 60px rgba(0,0,0,0.08);
```

Dark theme:

```css
--qp-bg: #0b0c0f;
--qp-surface: rgba(28,28,30,0.72);
--qp-surface-solid: #1c1c1e;
--qp-ink: #f5f5f7;
--qp-muted: #a1a1a6;
--qp-hairline: rgba(255,255,255,0.12);
--qp-accent: #409cff;
--qp-accent-soft: rgba(64,156,255,0.16);
--qp-safe: #35c759;
--qp-warn: #ffd60a;
--qp-danger: #ff453a;
--qp-shadow: 0 18px 70px rgba(0,0,0,0.42);
```

Background treatment:

- Light mode: soft off-white radial gradients, very subtle.
- Dark mode: graphite black with low-contrast blue glow.
- Avoid rainbow gradients and heavy blur.

### 5.4 Typography

Recommended:

- Use `Geist Sans` or `Inter` if installed locally through npm packages.
- Use `Geist Mono` or `JetBrains Mono` for JSON, status values, and code.
- Enable tabular numbers for financial and status values.

CSS:

```css
font-variant-numeric: tabular-nums;
text-rendering: geometricPrecision;
```

Hierarchy:

```txt
Display: 56/1.02, weight 650
H1: 40/1.1, weight 650
H2: 28/1.15, weight 620
H3: 20/1.25, weight 600
Body: 15-16/1.6, weight 400
Caption: 12-13/1.35, weight 500
Mono: 13/1.55
```

### 5.5 Components

Use shadcn/ui on top of Radix primitives for accessibility and speed, but customize the visual language.

Core components:

- `AppShell`
- `Sidebar`
- `TopStatusBar`
- `SafetyBanner`
- `HealthPill`
- `MetricCard`
- `GlassCard`
- `CommandButton`
- `JsonViewer`
- `EndpointResultPanel`
- `SchemaDrivenForm`
- `SignalCard`
- `SignalTable`
- `PolicyEditor`
- `RunTimeline`
- `JobStatusBadge`
- `ConfirmDialog`
- `EmptyState`
- `ErrorBoundary`

### 5.6 Motion

Use Framer Motion lightly.

Allowed:

- Page fade/slide: 120–220ms
- Card hover lift: translateY(-2px), shadow increase
- Drawer slide: 180–240ms
- Status pulse only for active jobs

Required:

- Respect `prefers-reduced-motion`.
- No infinite decorative animations except subtle loading states.
- Motion must clarify state changes.

### 5.7 Data visualization

Use Recharts or Tremor-style primitives.

Guidelines:

- Prioritize legibility over decoration.
- Always show labels and accessible descriptions.
- Use subtle grid lines.
- Do not show fake P&L or fake account equity unless backend returns fixture data and the UI labels it as fixture/mock.
- Include empty states and loading skeletons.

## 6. Interaction Design

### 6.1 Progressive disclosure

Default screens should be easy. Raw JSON should be available but tucked into drawers/collapsibles.

User path:

1. See simplified result.
2. Expand details.
3. Copy raw JSON.
4. Open API docs only when needed.

### 6.2 Confirmation and safety

Any action that triggers backend computation should show:

- What endpoint will be called
- What input will be sent
- Whether it is mock-only
- What can happen afterward

For confirm/run flows, use a clear modal with one primary button and one cancel button.

### 6.3 Error handling

Error cards must include:

- Human-readable summary
- HTTP status
- Endpoint
- Recovery action
- Raw error body toggle

Example:

> “The backend rejected this policy preview. Check the required fields below, or open raw error details.”

### 6.4 Loading states

Use skeleton screens. Avoid blank pages.

For job-style actions:

- Immediately show “Submitted”
- Then show timeline
- Poll `GET /api/jobs/{job_id}` if the backend returns a job id
- Stop polling on success/failure/timeout

### 6.5 Keyboard support

- `Cmd/Ctrl + K`: command palette
- `/`: focus search
- `Esc`: close drawer/dialog
- `R`: refresh current page, when focus is not in input
- `?`: shortcuts modal

All interactive controls must be reachable and usable by keyboard.

## 7. Technical Stack

Create the frontend inside:

```txt
quantpilot/apps/web
```

Recommended stack:

- Vite
- React
- TypeScript
- Tailwind CSS v4
- shadcn/ui
- Radix UI primitives
- TanStack Query
- React Hook Form
- Zod
- Recharts
- Framer Motion
- Lucide React
- Vitest + React Testing Library
- Playwright for E2E/smoke UI tests
- openapi-typescript or Hey API for API types/client generation

Do not manually duplicate OpenAPI schemas. Generate types from the backend:

```bash
npx openapi-typescript http://127.0.0.1:8010/openapi.json -o src/lib/openapi.d.ts
```

or:

```bash
npx @hey-api/openapi-ts -i http://127.0.0.1:8010/openapi.json -o src/client
```

Use whichever works best with the current OpenAPI output. Keep the generated client out of hand-edited business logic.

## 8. API Integration Rules

### 8.1 API base URL

Use:

```txt
VITE_API_BASE_URL=http://127.0.0.1:8010
```

Fallback:

```ts
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8010";
```

### 8.2 Health check

On app load:

- Call `GET /api/health`
- Show status
- Cache briefly
- Retry manually, not aggressively

### 8.3 CORS

If the browser blocks requests from `http://127.0.0.1:5173` to `http://127.0.0.1:8010`, add FastAPI CORS middleware for local development only:

```txt
http://127.0.0.1:5173
http://localhost:5173
```

Do not open wildcard CORS in production-like settings.

### 8.4 Request/response display

Every endpoint interaction should keep:

- request method
- endpoint path
- request body
- response body
- timestamp
- status code
- duration

Expose these in a “Developer details” drawer.

## 9. Security and Safety Requirements

Hard requirements:

- Do not implement live trading.
- Do not add broker credential forms.
- Do not add market order UI.
- Do not rename mock runs as live executions.
- Do not hide safe defaults.
- Do not generate fake live brokerage states.
- Do not store secrets in localStorage.

Required labels:

- `Mock Broker`
- `Live Trading Disabled`
- `Limit Orders Only`
- `Fixture Data`

Required warning copy:

> “This interface controls the local QuantPilot pre-harness. It does not place live broker orders.”

## 10. Accessibility Requirements

Target WCAG 2.2 AA.

Requirements:

- Semantic landmarks: `header`, `nav`, `main`, `aside`
- Keyboard navigable dialogs, dropdowns, and drawers
- Visible focus rings
- Minimum text contrast AA
- Non-color indicators for state
- Accessible names for icon buttons
- Screen-reader labels for charts
- `prefers-reduced-motion` support
- Forms with labels, descriptions, errors, and `aria-describedby`

Use Radix/shadcn primitives correctly instead of building inaccessible custom widgets from scratch.

## 11. Content Style

### 11.1 Voice

Korean UI is preferred, with English technical endpoint labels where useful.

Tone:

- calm
- factual
- safety-aware
- not hype-driven

### 11.2 Microcopy examples

Good:

- “백엔드 연결됨”
- “모의 브로커 활성”
- “실거래 비활성”
- “정책 미리보기”
- “신호 사유 보기”
- “Raw JSON 열기”
- “모의 Level 1-2 실행”

Bad:

- “지금 매수”
- “실시간 수익 폭발”
- “자동으로 돈 벌기”
- “라이브 주문 실행”
- “AI가 알아서 거래합니다”

## 12. Implementation Plan

### Phase 1 — Project scaffold

- Initialize Vite React TypeScript app in `quantpilot/apps/web`.
- Add Tailwind v4.
- Add shadcn/ui.
- Add routing, query client, error boundary.
- Add design tokens and app shell.

### Phase 2 — API client

- Start backend on port 8010.
- Generate types from `/openapi.json`.
- Implement `apiFetch` wrapper.
- Implement health query.
- Implement endpoint mutation hooks.

### Phase 3 — Core UX

- Overview page
- Safety banner
- Health card
- Run smoke card
- Developer details drawer

### Phase 4 — Operator workflows

- Research page
- Policy Studio
- Signal Board
- Level 1-2 Run
- Jobs & Logs

### Phase 5 — Polish

- Dark mode
- Motion
- Empty states
- Error states
- Command palette
- Responsive mobile layout

### Phase 6 — Testing

- Unit tests for API wrapper and UI components
- E2E:
  - app loads
  - health status renders
  - smoke test button calls endpoint
  - offline state appears when backend is down
- Build check
- Accessibility scan where possible

## 13. Acceptance Criteria

The implementation is acceptable when:

- `npm run dev` starts the frontend.
- User can open `http://127.0.0.1:5173`.
- The app shows QuantPilot, not Swagger.
- `/api/health` result appears in the UI.
- The UI clearly shows mock/safe mode.
- User can run smoke test from the UI.
- User can access Research, Policies, Signal Board, Level 1-2 Run, Jobs, Settings.
- No live trading controls exist.
- All backend errors have readable UI states.
- The frontend builds with `npm run build`.
- Existing Python tests still pass.
- The visual design feels cohesive, modern, and premium.

## 14. Anti-Patterns to Avoid

- Building only a thin Swagger wrapper.
- Hiding all useful information behind JSON.
- Creating fake portfolio/P&L screens not supported by backend.
- Copying Apple’s exact page designs or branding.
- Using excessive gradients, glass blur, and animations.
- Using red/green alone for financial meaning.
- Hardcoding endpoint request schemas instead of reading OpenAPI.
- Ignoring offline/backend-not-running states.
- Adding auth or broker credentials prematurely.

## 15. Reference Inspirations

Use these as inspiration, not as templates to copy:

- Apple Human Interface Guidelines: clarity, deference, depth, platform polish.
- Grafana dashboards: reusable panels, variable-driven dashboards, inspectable data.
- OpenBB: financial research workspace concept, analyst workflow orientation.
- Radix UI: accessible primitive-first component design.
- WCAG 2.2 and WAI-ARIA APG: accessibility baseline.
- Nielsen Norman Group heuristics: consistency, user control, recognition over recall, visible status.
- Hick-Hyman law: reduce decision friction by grouping choices and progressive disclosure.
- Fitts’s law: make primary actions large, nearby, and easy to target.
