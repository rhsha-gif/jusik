import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RunPage } from "@/pages/run";
import type { Level12MockExecutionResponse, UserPolicy } from "@/lib/types";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const POLICY: UserPolicy = {
  policy_id: "pol_1",
  user_id: "fixture-user",
  version: 1,
  market: "KR_STOCK",
  risk_profile: "moderate",
  max_positions: 8,
  max_position_weight: 0.15,
  max_sector_weight: 0.4,
  min_cash_weight: 0.2,
  daily_loss_limit: -0.03,
  monthly_loss_limit: -0.05,
  max_daily_orders: 3,
  max_daily_turnover: 3_000_000,
  single_order_cash_limit: 1_000_000,
  rebalance_frequency: "weekly",
  execution_mode: "approval_required",
  allowed_order_types: ["limit"],
  broker: "mock",
  preferred_themes: [],
  preferred_sectors: [],
  blocklist: [],
  kill_switch_engaged: false,
  created_at: "2026-06-15T00:00:00Z",
};

const MOCK_EXECUTION: Level12MockExecutionResponse = {
  policy: POLICY,
  strategy: {},
  universe: [],
  analyst_reports: [],
  signals: [],
  rebalance: {
    policy_id: "pol_1",
    policy_version: 1,
    portfolio_plan: {},
    suggestions: [],
    order_submission_enabled: false,
    created_at: "2026-06-15T00:00:00Z",
  },
  daily_report: {
    report_id: "rpt_1",
    user_id: "fixture-user",
    policy_id: "pol_1",
    summary: {},
    order_plan_ids: ["oplan_1"],
    fill_ids: ["fill_1"],
    audit_event_count: 10,
    live_trading_enabled: false,
    created_at: "2026-06-15T00:00:01Z",
  },
  order_submission_enabled: true,
  portfolio_plan: {},
  proposals: [],
  submitted_order_plans: [
    {
      order_plan_id: "oplan_1",
      policy_id: "pol_1",
      policy_version: 1,
      status: "filled",
      idempotency_key: "key",
      risk_check_id: "risk_1",
      risk_check_expires_at: "2026-06-15T00:10:00Z",
      blocked_reason: null,
      approved_by: "level_1_2_mock_execution:mock_policy_v1",
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:01Z",
      intent: {
        intent_id: "intent_1",
        symbol: "AAA",
        side: "buy",
        order_type: "limit",
        quantity: 10,
        limit_price: 100,
        notional: 1000,
        target_weight: 0.1,
        reason: "test",
        quote_time: "2026-06-15T00:00:00Z",
      },
    },
  ],
  broker_orders: [
    {
      broker_order_id: "bord_1",
      order_plan_id: "oplan_1",
      broker_mode: "mock",
      status: "accepted",
      accepted_at: "2026-06-15T00:00:01Z",
      broker_reference: "mock-oplan_1",
    },
  ],
  fills: [
    {
      fill_id: "fill_1",
      broker_order_id: "bord_1",
      order_plan_id: "oplan_1",
      symbol: "AAA",
      quantity: 10,
      price: 100,
      notional: 1000,
      filled_at: "2026-06-15T00:00:01Z",
    },
  ],
  blocked_proposals: [],
  auto_execution: {
    mode: "program_auto_trade",
    executed: true,
    signals_evaluated: 1,
    proposals: 1,
    auto_submitted: 1,
    filled: 1,
    blocked: 0,
    filled_notional: 1000,
    decisions: [
      {
        order_plan_id: "oplan_1",
        symbol: "AAA",
        side: "buy",
        action: "buy_ready",
        strength: 0.8,
        quantity: 10,
        notional: 1000,
        decision: "executed",
        reason: "프로그램 타이밍 판단",
        broker_order_id: "bord_1",
        blocked_reason: null,
      },
    ],
    live_trading_enabled: false,
  },
  data_mode: "fixture",
  broker: "mock",
  live_trading_enabled: false,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RunPage />
    </QueryClientProvider>,
  );
}

describe("RunPage mock execution", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls the auto-trade endpoint and renders the program decision + mock fills", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/health")) {
        return json({
          status: "ok",
          live_trading_enabled: false,
          default_broker: "mock",
          data_mode: "fixture",
          data_mode_safe: true,
        });
      }
      if (url.includes("/api/level-1-2/mock-execute") && init?.method === "POST") {
        return json(MOCK_EXECUTION);
      }
      return json({ detail: `unhandled ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    const button = await screen.findByRole("button", { name: /모의 자동매매 실행/ });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    expect(await screen.findByText("프로그램 자동 매매 판단")).toBeInTheDocument();
    expect(screen.getByText("상세 체결 내역")).toBeInTheDocument();
    expect(screen.getByText("broker: mock")).toBeInTheDocument();
    expect(screen.getByText("bord_1")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/api/level-1-2/mock-execute"))).toBe(true);
    });
  });
});
