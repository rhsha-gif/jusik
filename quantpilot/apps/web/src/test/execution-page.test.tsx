import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ExecutionPage } from "@/pages/execution";
import type { ApprovalTicketDecisionResult, TradeApprovalTicket } from "@/lib/types";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const TICKET: TradeApprovalTicket = {
  ticket_id: "atkt_1",
  user_id: "fixture-user",
  policy_id: "pol_1",
  policy_version: 1,
  order_plan_id: "oplan_1",
  data_mode: "live_trading_candidate",
  status: "pending",
  symbol: "AAA",
  side: "buy",
  quantity: 10,
  limit_price: 100,
  notional: 1000,
  reason: "test signal",
  requested_at: "2026-06-15T00:00:00Z",
  expires_at: "2026-06-15T00:30:00Z",
  approved_at: null,
  approved_by: null,
  rejected_at: null,
  rejection_reason: null,
  submitted_at: null,
  submitted_order_plan_id: null,
  broker_order_id: null,
  blocked_reason: null,
  live_trading_enabled: false,
};

const BLOCKED_APPROVAL: ApprovalTicketDecisionResult = {
  ticket: { ...TICKET, status: "blocked", blocked_reason: "live_broker_unavailable" },
  order_plan: {
    order_plan_id: "oplan_1",
    policy_id: "pol_1",
    policy_version: 1,
    status: "proposed",
    idempotency_key: "key",
    risk_check_id: "risk_1",
    risk_check_expires_at: "2026-06-15T00:10:00Z",
    blocked_reason: null,
    approved_by: null,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    intent: {
      intent_id: "intent_1",
      symbol: "AAA",
      side: "buy",
      order_type: "limit",
      quantity: 10,
      limit_price: 100,
      notional: 1000,
      target_weight: 0.1,
      reason: "test signal",
      quote_time: "2026-06-15T00:00:00Z",
    },
  },
  broker_order: null,
  fills: [],
  live_trading_enabled: false,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExecutionPage />
    </QueryClientProvider>,
  );
}

describe("ExecutionPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders pending tickets and sends approve/reject requests", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api/execution/approval-tickets/pending")) {
        return json([TICKET]);
      }
      if (url.includes("/api/execution/approval-tickets/atkt_1/approve-and-submit") && method === "POST") {
        return json(BLOCKED_APPROVAL);
      }
      if (url.includes("/api/execution/approval-tickets/atkt_1/reject") && method === "POST") {
        return json({ ...TICKET, status: "rejected", rejection_reason: "user_rejected" });
      }
      return json({ detail: `unhandled ${method} ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("AAA")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /승인 후 시스템 제출/ }));
    expect((await screen.findAllByText(/live_broker_unavailable/)).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /거절/ }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/reject"))).toBe(true);
    });
  });
});
