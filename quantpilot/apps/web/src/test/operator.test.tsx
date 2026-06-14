import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { OperatorPage } from "@/pages/operator";
import type {
  OperatorReport,
  OperatorRunResult,
  OperatorStatusResponse,
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
