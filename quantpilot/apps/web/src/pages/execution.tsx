import { useEffect, useState } from "react";
import {
  ArrowRight,
  BellRing,
  Bot,
  CheckCircle2,
  Radar,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonViewer } from "@/components/json-viewer";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import {
  useApproveApprovalTicket,
  useGenerateApprovalTickets,
  usePendingApprovalTickets,
  useRejectApprovalTicket,
} from "@/lib/queries";
import {
  notifyApprovalTicket,
  requestApprovalNotificationPermission,
} from "@/lib/browser-notifications";
import { useWorkingPolicy } from "@/lib/working-policy";
import type { ApprovalDataMode, TradeApprovalTicket } from "@/lib/types";
import { cn, formatKRW, formatTime } from "@/lib/utils";

const DATA_MODE_LABEL: Record<ApprovalDataMode, string> = {
  fixture: "Fixture mock",
  paper_trading: "Paper trading",
  live_trading_candidate: "Live candidate",
};

export function ExecutionPage() {
  const workingPolicy = useWorkingPolicy();
  const pending = usePendingApprovalTickets();
  const generate = useGenerateApprovalTickets();
  const approve = useApproveApprovalTicket();
  const reject = useRejectApprovalTicket();
  const [dataMode, setDataMode] = useState<ApprovalDataMode>("live_trading_candidate");
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const requestBody = {
    policy_id: workingPolicy?.policyId ?? null,
    data_mode: dataMode,
  };

  useEffect(() => {
    if (!notificationsEnabled || !pending.data?.length) return;
    let sent = 0;
    for (const ticket of pending.data) {
      if (notifyApprovalTicket(ticket) === "sent") sent += 1;
    }
    if (sent > 0) setNotice(`브라우저 알림 ${sent}건을 보냈습니다.`);
  }, [notificationsEnabled, pending.data]);

  const enableNotifications = async () => {
    const permission = await requestApprovalNotificationPermission();
    if (permission === "granted") {
      setNotificationsEnabled(true);
      setNotice("브라우저 알림이 켜졌습니다.");
      return;
    }
    setNotificationsEnabled(false);
    setNotice(permission === "unsupported" ? "이 브라우저는 알림을 지원하지 않습니다." : "알림 권한이 허용되지 않았습니다.");
  };

  return (
    <>
      <PageHeader
        eyebrow="Approval Rails"
        title="매매 승인 알림"
        description="실거래 단계에서는 프로그램이 매매 타이밍을 포착하면 사용자에게 승인 알림을 보냅니다. 사용자가 직접 매매하지 않고, 승인하면 시스템이 같은 경로로 주문을 직접 집행합니다. 현재 live broker는 연결되어 있지 않아 live 후보는 제출 전 차단됩니다."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => void pending.refetch()} disabled={pending.isFetching}>
              <RefreshCw className={pending.isFetching ? "animate-spin" : ""} /> 새로고침
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void enableNotifications()}>
              <BellRing /> 브라우저 알림 켜기
            </Button>
          </div>
        }
      />

      <FlowSteps />

      <Card>
        <CardHeader>
          <CardTitle>승인 티켓 생성</CardTitle>
          <CardDescription>
            현재 정책과 신호로 주문 제안을 만들고, 선택한 실행 라벨의 승인 티켓을 생성합니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            {(Object.keys(DATA_MODE_LABEL) as ApprovalDataMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setDataMode(mode)}
                className={cn(
                  "rounded-full border px-3.5 py-2 text-[13px] font-medium transition-colors",
                  dataMode === mode
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-hairline text-muted hover:text-ink",
                )}
              >
                {DATA_MODE_LABEL[mode]}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="neutral">policy: {workingPolicy?.policyId ?? "latest"}</Badge>
            <Badge variant={dataMode === "live_trading_candidate" ? "warn" : "safe"}>
              {DATA_MODE_LABEL[dataMode]}
            </Badge>
            <Badge variant="safe">live_trading_enabled: false</Badge>
          </div>
          <Button
            onClick={() => generate.mutate(requestBody)}
            disabled={generate.isPending}
            className="w-fit"
          >
            <BellRing /> {generate.isPending ? "생성 중…" : "승인 티켓 생성"}
          </Button>
          {notice && <p className="text-[12.5px] text-muted">{notice}</p>}
          {generate.isError && (
            <ErrorState error={generate.error} context="승인 티켓 생성에 실패했습니다" />
          )}
          {generate.isSuccess && (
            <div className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-3 text-[13px]">
              생성된 티켓: {generate.data.length}건
            </div>
          )}
        </CardContent>
      </Card>

      <PendingTicketsCard
        tickets={pending.data ?? []}
        pending={pending.isPending}
        onApprove={(ticket) => approve.mutate({ ticketId: ticket.ticket_id, approvedBy: "user" })}
        onReject={(ticket) => reject.mutate({ ticketId: ticket.ticket_id, reason: "user_rejected" })}
        busy={approve.isPending || reject.isPending}
      />

      {pending.isError && <ErrorState error={pending.error} context="승인 대기 목록을 불러오지 못했습니다" />}
      {approve.isError && <ErrorState error={approve.error} context="승인 제출에 실패했습니다" />}
      {reject.isError && <ErrorState error={reject.error} context="승인 거절에 실패했습니다" />}

      {approve.isSuccess && (
        <Card>
          <CardHeader>
            <CardTitle>최근 승인 결과</CardTitle>
            <CardDescription>
              티켓 상태: {approve.data.ticket.status}
              {approve.data.ticket.blocked_reason ? ` · ${approve.data.ticket.blocked_reason}` : ""}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <JsonViewer data={approve.data} title="Raw JSON (approval result)" defaultOpen />
          </CardContent>
        </Card>
      )}
    </>
  );
}

const FLOW_STEPS = [
  { icon: Radar, title: "타이밍 포착", desc: "프로그램이 매매 시점을 판단" },
  { icon: BellRing, title: "승인 알림", desc: "사용자에게 알림 전송" },
  { icon: CheckCircle2, title: "사용자 승인", desc: "승인 또는 거절 선택" },
  { icon: Bot, title: "시스템 집행", desc: "승인 시 시스템이 직접 주문" },
] as const;

/** Visual contract of the real-trading approval rail: who decides what, in order. */
function FlowSteps() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-stretch sm:gap-0">
        {FLOW_STEPS.map((step, index) => {
          const Icon = step.icon;
          const isExecute = index === FLOW_STEPS.length - 1;
          return (
            <div key={step.title} className="flex flex-1 items-center gap-3">
              <div className="flex items-start gap-3">
                <span
                  aria-hidden
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-xl",
                    isExecute ? "bg-accent text-white" : "bg-accent-soft text-accent",
                  )}
                >
                  <Icon className="size-4.5" />
                </span>
                <div className="leading-tight">
                  <p className="text-[13.5px] font-semibold">
                    <span className="mr-1 text-faint">{index + 1}</span>
                    {step.title}
                  </p>
                  <p className="mt-0.5 text-[12px] text-muted">{step.desc}</p>
                </div>
              </div>
              {index < FLOW_STEPS.length - 1 && (
                <ArrowRight aria-hidden className="mx-auto hidden size-4 shrink-0 text-faint sm:block" />
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function PendingTicketsCard({
  tickets,
  pending,
  busy,
  onApprove,
  onReject,
}: {
  tickets: TradeApprovalTicket[];
  pending: boolean;
  busy: boolean;
  onApprove: (ticket: TradeApprovalTicket) => void;
  onReject: (ticket: TradeApprovalTicket) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>승인 대기 티켓</CardTitle>
        <CardDescription>브라우저 알림을 켜면 새 pending 티켓이 세션당 한 번씩 알림으로 표시됩니다.</CardDescription>
      </CardHeader>
      <CardContent>
        {pending ? (
          <p className="text-[13px] text-muted">불러오는 중…</p>
        ) : tickets.length === 0 ? (
          <p className="text-[13px] text-muted">승인 대기 티켓이 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-hairline text-[12px] text-muted">
                  <th className="py-2.5 pr-4 font-medium">상태</th>
                  <th className="py-2.5 pr-4 font-medium">종목</th>
                  <th className="py-2.5 pr-4 font-medium">라벨</th>
                  <th className="py-2.5 pr-4 font-medium">주문</th>
                  <th className="py-2.5 pr-4 font-medium">만료</th>
                  <th className="py-2.5 font-medium">액션</th>
                </tr>
              </thead>
              <tbody>
                {tickets.map((ticket) => (
                  <tr
                    key={ticket.ticket_id}
                    className="border-b border-hairline/60 align-top transition-colors hover:bg-surface-solid/70"
                  >
                    <td className="py-3 pr-4">
                      <Badge variant={ticket.data_mode === "live_trading_candidate" ? "warn" : "safe"}>
                        {ticket.status}
                      </Badge>
                    </td>
                    <td className="py-3 pr-4">
                      <p className="font-mono font-semibold">{ticket.symbol}</p>
                      <p className="mt-0.5 font-mono text-[11.5px] text-muted">{ticket.ticket_id}</p>
                    </td>
                    <td className="py-3 pr-4">
                      <Badge variant={ticket.data_mode === "live_trading_candidate" ? "warn" : "neutral"}>
                        {ticket.data_mode}
                      </Badge>
                      {ticket.data_mode === "live_trading_candidate" && (
                        <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-warn">
                          <ShieldAlert className="size-3.5" /> 승인 후 live 제출은 차단됩니다.
                        </p>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <p>
                        {ticket.side.toUpperCase()} {ticket.quantity.toLocaleString()}주
                      </p>
                      <p className="mt-0.5 text-[12px] text-muted">{formatKRW(ticket.notional)}</p>
                    </td>
                    <td className="py-3 pr-4 text-[12px] text-muted">{formatTime(ticket.expires_at)}</td>
                    <td className="py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" onClick={() => onApprove(ticket)} disabled={busy}>
                          <CheckCircle2 /> 승인 후 시스템 제출
                        </Button>
                        <Button size="sm" variant="danger" onClick={() => onReject(ticket)} disabled={busy}>
                          <XCircle /> 거절
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
