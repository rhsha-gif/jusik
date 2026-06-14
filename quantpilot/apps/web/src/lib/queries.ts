import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type {
  AnalystResponse,
  HealthResponse,
  IntentRunRequest,
  IntentRunResponse,
  Level12Request,
  Level12RunResponse,
  ParsePolicyRequest,
  PolicyPreviewResponse,
  SignalBoardResponse,
  SmokeResult,
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

export function useIntentRun() {
  return useMutation({
    mutationFn: (body: IntentRunRequest) =>
      apiFetch<IntentRunResponse>("/api/intent/run", { method: "POST", body }),
  });
}
