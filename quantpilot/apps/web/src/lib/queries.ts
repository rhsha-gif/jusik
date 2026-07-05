import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type {
  AnalystResponse,
  ApprovalTicketDecisionResult,
  ApprovalTicketGenerateRequest,
  HealthResponse,
  Level12Request,
  Level12MockExecutionResponse,
  Level12RunResponse,
  OperatorReportEnvelope,
  OperatorRunRequest,
  OperatorRunResult,
  OperatorStatusResponse,
  ParsePolicyRequest,
  PolicyPreviewResponse,
  SignalBoardResponse,
  SmokeResult,
  TradeApprovalTicket,
  UniverseResponse,
  UserPolicy,
} from "./types";

export const DEFAULT_POLICY_TEXT =
  "KR stock moderate risk weekly rebalance, approval required, mock broker, limit orders only.";

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiFetch<HealthResponse>("/api/health", { signal }),
    refetchInterval: 30_000,
    retry: 1,
    staleTime: 10_000,
  });
}

export function useRunSmoke() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<SmokeResult>("/api/harness/run-smoke", { method: "POST", body: {} }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

export function usePolicyPreview() {
  return useMutation({
    mutationFn: (body: ParsePolicyRequest) =>
      apiFetch<PolicyPreviewResponse>("/api/policies/preview", { method: "POST", body }),
  });
}

export function usePolicyParse() {
  return useMutation({
    mutationFn: (body: ParsePolicyRequest) =>
      apiFetch<UserPolicy>("/api/policies/parse", { method: "POST", body }),
  });
}

export function usePolicyConfirm() {
  return useMutation({
    mutationFn: (policyId: string) =>
      apiFetch<UserPolicy>("/api/policies/confirm", {
        method: "POST",
        body: { policy_id: policyId },
      }),
  });
}

export function useResearchUniverse() {
  return useMutation({
    mutationFn: (body: Level12Request) =>
      apiFetch<UniverseResponse>("/api/research/universe", { method: "POST", body }),
  });
}

export function useResearchAnalyst() {
  return useMutation({
    mutationFn: (body: Level12Request) =>
      apiFetch<AnalystResponse>("/api/research/analyst", { method: "POST", body }),
  });
}

export function useSignalBoard() {
  return useMutation({
    mutationFn: (body: Level12Request) =>
      apiFetch<SignalBoardResponse>("/api/signals/board", { method: "POST", body }),
  });
}

export function useLevel12Run() {
  return useMutation({
    mutationFn: (body: Level12Request) =>
      apiFetch<Level12RunResponse>("/api/level-1-2/run", { method: "POST", body }),
  });
}

export function useLevel12MockExecute() {
  return useMutation({
    mutationFn: (body: Level12Request & { partial_allow?: boolean }) =>
      apiFetch<Level12MockExecutionResponse>("/api/level-1-2/mock-execute", {
        method: "POST",
        body,
      }),
  });
}

export const APPROVAL_TICKETS_PENDING_KEY = ["execution", "approval-tickets", "pending"] as const;

export function usePendingApprovalTickets() {
  return useQuery({
    queryKey: APPROVAL_TICKETS_PENDING_KEY,
    queryFn: ({ signal }) =>
      apiFetch<TradeApprovalTicket[]>("/api/execution/approval-tickets/pending", { signal }),
    retry: 1,
    staleTime: 5_000,
    // Poll so the global approval-alert bell in the app shell stays fresh — a
    // new pending ticket is the "trade timing detected" alarm for real trading.
    refetchInterval: 20_000,
  });
}

export function useGenerateApprovalTickets() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ApprovalTicketGenerateRequest) =>
      apiFetch<TradeApprovalTicket[]>("/api/execution/approval-tickets/generate", {
        method: "POST",
        body,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: APPROVAL_TICKETS_PENDING_KEY });
    },
  });
}

export function useApproveApprovalTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ticketId, approvedBy }: { ticketId: string; approvedBy?: string }) =>
      apiFetch<ApprovalTicketDecisionResult>(
        `/api/execution/approval-tickets/${ticketId}/approve-and-submit`,
        {
          method: "POST",
          body: { approved_by: approvedBy ?? "user" },
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: APPROVAL_TICKETS_PENDING_KEY });
    },
  });
}

export function useRejectApprovalTicket() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ticketId, reason }: { ticketId: string; reason?: string }) =>
      apiFetch<TradeApprovalTicket>(`/api/execution/approval-tickets/${ticketId}/reject`, {
        method: "POST",
        body: { reason: reason ?? "user_rejected" },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: APPROVAL_TICKETS_PENDING_KEY });
    },
  });
}

export const OPERATOR_STATUS_KEY = ["operator", "status"] as const;
export const OPERATOR_REPORT_LATEST_KEY = ["operator", "report", "latest"] as const;

export function useOperatorStatus() {
  return useQuery({
    queryKey: OPERATOR_STATUS_KEY,
    queryFn: ({ signal }) =>
      apiFetch<OperatorStatusResponse>("/api/operator/status", { signal }),
    retry: 1,
    staleTime: 10_000,
  });
}

export function useLatestOperatorReport() {
  return useQuery({
    queryKey: OPERATOR_REPORT_LATEST_KEY,
    queryFn: ({ signal }) =>
      apiFetch<OperatorReportEnvelope>("/api/operator/reports/latest", { signal }),
    retry: 1,
    staleTime: 10_000,
  });
}

export function useOperatorRunOnce() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OperatorRunRequest) =>
      apiFetch<OperatorRunResult>("/api/operator/run-once", { method: "POST", body }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: OPERATOR_STATUS_KEY });
      void queryClient.invalidateQueries({ queryKey: OPERATOR_REPORT_LATEST_KEY });
    },
  });
}
