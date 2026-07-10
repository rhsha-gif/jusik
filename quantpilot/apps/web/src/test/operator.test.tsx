import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { OperatorPage } from "@/pages/operator";
import type {
  OperatorReport,
  OperatorRunResult,
  OperatorStatusResponse,
  ProfessionalOperatorStatusResponse,
} from "@/lib/types";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SAFE_STATUS: OperatorStatusResponse = {
  live_trading_enabled: false,
  feature_flags: {
    LIVE_TRADING_ENABLED: false,
    FULLY_AUTOMATED_OPERATOR_ENABLED: false,
    OPERATOR_KILL_SWITCH: false,
  },
  registry: [
    {
      strategy_id: "strat_demo",
      version: "1.0.0",
      status: "validated_l5",
      allowed_execution_levels: ["level_5", "fully_automated"],
      disabled_reason: null,
    },
  ],
  runs: 0,
};

const UNAVAILABLE_PROFESSIONAL: ProfessionalOperatorStatusResponse = {
  available: false,
  overall_status: "unavailable",
  reason_code: "paper_state_db_not_configured",
  source: "paper_state_sqlite",
  observed_at: "2026-07-10T06:00:00Z",
  live_trading_enabled: false,
  schema_version: null,
  freshness: {
    status: "unavailable",
    latest_evidence_at: null,
    stale_after_seconds: 180,
    reason_codes: ["paper_state_db_not_configured"],
  },
  safety: {
    status: "unavailable",
    policies: [],
    latest_session: {
      status: "none",
      started_at: null,
      lease_expires_at: null,
      updated_at: null,
      lease_valid: false,
    },
    reason_codes: ["paper_state_db_not_configured"],
  },
  positions: [],
  strategy_health: [],
  rebalance: [],
  reconciliation: {
    status: "unavailable",
    unresolved_count: 0,
    blocked_count: 0,
    outcome_unknown_count: 0,
    dispatches: [],
    pending_liquidations: [],
    reason_codes: ["paper_state_db_not_configured"],
  },
};

const CRITICAL_PROFESSIONAL: ProfessionalOperatorStatusResponse = {
  ...UNAVAILABLE_PROFESSIONAL,
  available: true,
  overall_status: "critical",
  reason_code: null,
  schema_version: 8,
  freshness: {
    status: "fresh",
    latest_evidence_at: "2026-07-10T05:59:50Z",
    stale_after_seconds: 180,
    reason_codes: [],
  },
  safety: {
    status: "safe",
    policies: [
      {
        policy_id: "policy-1",
        status: "safe",
        autopilot_paused: false,
        broker_healthy: true,
        updated_at: "2026-07-10T05:59:50Z",
        stale: false,
        reason_codes: [],
      },
    ],
    latest_session: {
      status: "closed",
      started_at: "2026-07-10T05:58:00Z",
      lease_expires_at: "2026-07-10T06:03:00Z",
      updated_at: "2026-07-10T05:59:55Z",
      lease_valid: false,
    },
    reason_codes: [],
  },
  positions: [
    {
      policy_id: "policy-1",
      policy_version: 1,
      strategy_id: "pullback_trend_v2",
      strategy_version: "2.0",
      symbol: "005930",
      quantity: 3,
      average_entry_price: 70000,
      atr14: 1200,
      active_stop: 64400,
      attribution_status: "active",
      reconciled_at: "2026-07-10T05:59:50Z",
      status: "safe",
      stale: false,
      reason_codes: [],
    },
  ],
  strategy_health: [
    {
      policy_id: "policy-1",
      strategy_id: "pullback_trend_v2",
      strategy_version: "2.0",
      health_status: "active",
      retirement_phase: "none",
      pending_order_count: 0,
      last_risk_evaluated_at: "2026-07-10T05:59:00Z",
      updated_at: "2026-07-10T05:59:50Z",
      status: "safe",
      stale: false,
      reason_codes: [],
    },
  ],
  rebalance: [
    {
      policy_id: "policy-1",
      strategy_id: "pullback_trend_v2",
      strategy_version: "2.0",
      current_week: "2026-W28",
      last_rebalance_session: "2026-W28",
      claim_status: "completed",
      claimed_at: "2026-07-10T05:58:00Z",
      completed_at: "2026-07-10T05:58:30Z",
      status: "safe",
      reason_codes: [],
    },
  ],
  reconciliation: {
    status: "critical",
    unresolved_count: 1,
    blocked_count: 0,
    outcome_unknown_count: 1,
    dispatches: [
      {
        order_plan_id: "order-1",
        policy_id: "policy-1",
        strategy_id: "pullback_trend_v2",
        strategy_version: "2.0",
        symbol: "005930",
        side: "sell",
        purpose: "protective_exit",
        status: "outcome_unknown",
        reconciliation_status: "pending",
        quantity: 2,
        cumulative_filled_quantity: 0,
        remaining_quantity: 2,
        updated_at: "2026-07-10T05:59:50Z",
        last_error_code: "broker_response_ambiguous",
      },
    ],
    pending_liquidations: [],
    reason_codes: ["paper_submission_outcome_unknown"],
  },
};

function makeReport(overrides: Partial<OperatorReport> = {}): OperatorReport {
  return {
    report_id: "oprpt_1",
    run_id: "oprun_1",
    user_id: "fixture-user",
    policy_id: "policy_1",
    policy_version: 1,
    started_at: "2026-06-15T00:00:00Z",
    completed_at: "2026-06-15T00:00:01Z",
    status: "completed",
    strategy_selection: {
      selected_strategy_id: "strat_demo",
      selected_version: "1.0.0",
      eligible_strategy_ids: ["strat_demo"],
      rejected: {},
      reason: "selected eligible strategy",
    },
    decisions: [],
    fallback: null,
    order_plan_ids: [],
    broker_order_ids: [],
    risk_check_ids: [],
    safety_flags: { LIVE_TRADING_ENABLED: false, BROKER_MODE: "mock" },
    live_trading_enabled: false,
    audit_event_count: 0,
    ...overrides,
  };
}

interface FetchRoutes {
  status?: OperatorStatusResponse;
  professional?: ProfessionalOperatorStatusResponse;
  latest?: { report: OperatorReport | null; text: string };
  runOnce?: OperatorRunResult;
  onRunOnce?: (body: unknown) => void;
}

function stubFetch(routes: FetchRoutes) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/api/operator/run-once") && method === "POST") {
      routes.onRunOnce?.(init?.body ? JSON.parse(String(init.body)) : null);
      return json(routes.runOnce ?? { detail: "no run configured" }, routes.runOnce ? 200 : 500);
    }
    if (url.includes("/api/operator/reports/latest")) {
      return json(routes.latest ?? { report: null, text: "" });
    }
    if (url.includes("/api/operator/professional-status")) {
      return json(routes.professional ?? UNAVAILABLE_PROFESSIONAL);
    }
    if (url.includes("/api/operator/status")) {
      return json(routes.status ?? SAFE_STATUS);
    }
    return json({ detail: `unhandled ${method} ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OperatorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OperatorPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("queries operator status and latest report on mount", async () => {
    const fetchMock = stubFetch({ status: SAFE_STATUS });
    renderPage();

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((u) => u.includes("/api/operator/status"))).toBe(true);
      expect(urls.some((u) => u.includes("/api/operator/reports/latest"))).toBe(true);
    });
  });

  it("renders the safe default state with no live-trading affordance", async () => {
    stubFetch({ status: SAFE_STATUS });
    renderPage();

    expect(await screen.findByText("live_trading_enabled: false")).toBeInTheDocument();
    expect(screen.getByText("LIVE_TRADING_ENABLED: false")).toBeInTheDocument();
    // Default run mode is the safe dry_run; no submission affordance is shown.
    expect(
      screen.getByText(/Dry Run: 주문이 제출되지 않습니다/),
    ).toBeInTheDocument();
  });

  it("renders an explicit unavailable professional state instead of a green empty state", async () => {
    stubFetch({ status: SAFE_STATUS, professional: UNAVAILABLE_PROFESSIONAL });
    renderPage();

    expect(await screen.findByText("Paper 상태 DB가 설정되지 않았거나 안전하게 읽을 수 없습니다. 빈 값을 정상 상태로 간주하지 않습니다.")).toBeInTheDocument();
    expect(screen.getByText("paper_state_db_not_configured")).toBeInTheDocument();
  });

  it("renders all five durable professional status sections without fabricated PnL", async () => {
    stubFetch({ status: SAFE_STATUS, professional: CRITICAL_PROFESSIONAL });
    renderPage();

    expect(await screen.findByRole("heading", { name: "안전 상태" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "포지션 위험" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "전략 건강" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "주간 리밸런싱" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "주문 조정 (Reconciliation)" })).toBeInTheDocument();
    expect(screen.getByText("outcome_unknown / pending")).toBeInTheDocument();
    expect(screen.getByText(/현재가·평가손익·스톱까지 거리는/)).toBeInTheDocument();
    expect(screen.queryByText(/수익률:/)).not.toBeInTheDocument();
  });

  it("labels stale professional evidence as delayed data", async () => {
    const stale: ProfessionalOperatorStatusResponse = {
      ...CRITICAL_PROFESSIONAL,
      overall_status: "attention",
      freshness: {
        ...CRITICAL_PROFESSIONAL.freshness,
        status: "stale",
        reason_codes: ["durable_evidence_stale"],
      },
      reconciliation: {
        ...CRITICAL_PROFESSIONAL.reconciliation,
        status: "safe",
        unresolved_count: 0,
        outcome_unknown_count: 0,
        dispatches: [],
        reason_codes: [],
      },
    };
    stubFetch({ status: SAFE_STATUS, professional: stale });
    renderPage();

    expect(await screen.findByText("지연 데이터")).toBeInTheDocument();
  });

  it("builds a dry_run OperatorRunRequest with a fresh idempotency key", async () => {
    let captured: Record<string, unknown> | null = null;
    stubFetch({
      status: SAFE_STATUS,
      runOnce: {
        run_id: "oprun_1",
        status: "completed",
        submitted_order_plan_ids: [],
        blocked_order_plan_ids: [],
        fallback: null,
        report: makeReport(),
      },
      onRunOnce: (body) => {
        captured = body as Record<string, unknown>;
      },
    });
    renderPage();

    fireEvent.change(await screen.findByLabelText(/policy_id/), {
      target: { value: "policy_1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /1회 실행/ }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      policy_id: "policy_1",
      run_mode: "dry_run",
      requested_policy_version: 1,
    });
    expect(typeof captured!.idempotency_key).toBe("string");
    expect((captured!.idempotency_key as string).length).toBeGreaterThan(0);
    expect(typeof captured!.requested_at).toBe("string");
  });

  it("renders fallback reason and detail from a fallback run result", async () => {
    stubFetch({
      status: SAFE_STATUS,
      runOnce: {
        run_id: "oprun_2",
        status: "fallback",
        submitted_order_plan_ids: [],
        blocked_order_plan_ids: [],
        fallback: {
          from_level: 5,
          to_level: 3,
          reason_code: "broker_unhealthy",
          detail: "broker health check failed",
          order_submission_enabled: false,
        },
        report: makeReport({
          status: "fallback",
          decisions: [
            {
              decision_id: "opdec_1",
              run_id: "oprun_2",
              policy_id: "policy_1",
              policy_version: 1,
              strategy_id: "strat_demo",
              order_plan_id: null,
              action: "fallback",
              reason: "broker unhealthy, falling back",
              risk_check_id: null,
              created_at: "2026-06-15T00:00:01Z",
            },
          ],
        }),
      },
    });
    renderPage();

    fireEvent.change(await screen.findByLabelText(/policy_id/), {
      target: { value: "policy_1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /1회 실행/ }));

    expect(await screen.findByText(/broker_unhealthy/)).toBeInTheDocument();
    expect(screen.getByText("broker health check failed")).toBeInTheDocument();
    expect(screen.getByText("broker unhealthy, falling back")).toBeInTheDocument();
  });

  it("renders the latest report text when present", async () => {
    stubFetch({
      status: SAFE_STATUS,
      latest: { report: makeReport(), text: "운영자 리포트 텍스트 렌더링" },
    });
    renderPage();

    expect(await screen.findByText("운영자 리포트 텍스트 렌더링")).toBeInTheDocument();
  });
});
