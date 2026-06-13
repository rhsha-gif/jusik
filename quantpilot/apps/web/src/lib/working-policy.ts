import { useSyncExternalStore } from "react";
import type { UserPolicy } from "./types";

/**
 * The "working policy" the operator carries across pages.
 *
 * Most workflow endpoints accept an optional policy_id; when the user
 * parses/confirms a policy in Policy Studio we pin it here so Research,
 * Signal Board, and Level 1-2 Run reuse the same policy by default.
 * Only non-sensitive fixture metadata is stored.
 */
export interface WorkingPolicy {
  policyId: string;
  confirmed: boolean;
  market: string;
  riskProfile: string;
  executionMode: string;
  broker: string;
  savedAt: string;
}

const STORAGE_KEY = "qp.workingPolicy";
const listeners = new Set<() => void>();
let cache: WorkingPolicy | null = readStorage();

function readStorage(): WorkingPolicy | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as WorkingPolicy) : null;
  } catch {
    return null;
  }
}

function emit() {
  for (const listener of listeners) listener();
}

export function setWorkingPolicy(policy: UserPolicy, confirmed: boolean) {
  cache = {
    policyId: policy.policy_id,
    confirmed,
    market: policy.market,
    riskProfile: policy.risk_profile,
    executionMode: policy.execution_mode,
    broker: policy.broker,
    savedAt: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  emit();
}

export function markWorkingPolicyConfirmed(policyId: string) {
  if (cache && cache.policyId === policyId) {
    cache = { ...cache, confirmed: true };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
    emit();
  }
}

export function clearWorkingPolicy() {
  cache = null;
  localStorage.removeItem(STORAGE_KEY);
  emit();
}

export function getWorkingPolicy(): WorkingPolicy | null {
  return cache;
}

export function useWorkingPolicy(): WorkingPolicy | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => cache,
  );
}
