import { afterEach, describe, expect, it, vi } from "vitest";
import {
  notifyApprovalTicket,
  requestApprovalNotificationPermission,
  resetApprovalNotificationDedupForTests,
} from "@/lib/browser-notifications";
import type { TradeApprovalTicket } from "@/lib/types";

function ticket(id = "atkt_1"): TradeApprovalTicket {
  return {
    ticket_id: id,
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
    reason: "test",
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
}

describe("browser approval notifications", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    resetApprovalNotificationDedupForTests();
  });

  it("falls back when browser notifications are unsupported", async () => {
    Object.defineProperty(window, "Notification", { value: undefined, configurable: true });

    await expect(requestApprovalNotificationPermission()).resolves.toBe("unsupported");
    expect(notifyApprovalTicket(ticket())).toBe("unsupported");
  });

  it("requests permission and suppresses duplicate ticket notifications", async () => {
    const created: unknown[] = [];
    class FakeNotification {
      static permission: NotificationPermission = "default";
      static requestPermission = vi.fn(async () => {
        FakeNotification.permission = "granted";
        return "granted" as NotificationPermission;
      });

      constructor(title: string, options?: NotificationOptions) {
        created.push({ title, options });
      }
    }
    Object.defineProperty(window, "Notification", { value: FakeNotification, configurable: true });

    await expect(requestApprovalNotificationPermission()).resolves.toBe("granted");
    expect(notifyApprovalTicket(ticket())).toBe("sent");
    expect(notifyApprovalTicket(ticket())).toBe("duplicate");
    expect(created).toHaveLength(1);
  });
});
