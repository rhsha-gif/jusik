import type { TradeApprovalTicket } from "./types";

export type NotificationPermissionState = NotificationPermission | "unsupported";
export type ApprovalNotificationResult =
  | "sent"
  | "duplicate"
  | "unsupported"
  | "permission_denied";

const notifiedTicketIds = new Set<string>();

function browserNotification(): typeof Notification | null {
  if (typeof window === "undefined" || !("Notification" in window)) return null;
  return window.Notification;
}

export async function requestApprovalNotificationPermission(): Promise<NotificationPermissionState> {
  const NotificationCtor = browserNotification();
  if (!NotificationCtor) return "unsupported";
  if (NotificationCtor.permission === "granted") return "granted";
  if (NotificationCtor.permission === "denied") return "denied";
  return NotificationCtor.requestPermission();
}

export function notifyApprovalTicket(ticket: TradeApprovalTicket): ApprovalNotificationResult {
  const NotificationCtor = browserNotification();
  if (!NotificationCtor) return "unsupported";
  if (NotificationCtor.permission !== "granted") return "permission_denied";
  if (notifiedTicketIds.has(ticket.ticket_id)) return "duplicate";
  notifiedTicketIds.add(ticket.ticket_id);
  new NotificationCtor("매매 승인 대기", {
    body: `${ticket.symbol} ${ticket.side.toUpperCase()} ${ticket.quantity.toLocaleString()}주 · ${ticket.data_mode}`,
    tag: ticket.ticket_id,
  });
  return "sent";
}

export function resetApprovalNotificationDedupForTests() {
  notifiedTicketIds.clear();
}
