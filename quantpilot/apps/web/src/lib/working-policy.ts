import { useSyncExternalStore } from "react";
import type { UserPolicy } from "./types";
import { clearRunSnapshot } from "./run-store";

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
  version: number;
  confirmed: boolean;
  market: string;
  riskProfile: string;
  executionMode: string;
  broker: string;
  direction?: string;
  symbols?: string[];
  sectors?: string[];
  themes?: string[];
  generatedPolicyText?: string;
  savedAt: string;
}

export interface WorkingPolicyMetadata {
  direction?: string;
  symbols?: string[];
  sectors?: string[];
  themes?: string[];
  generatedPolicyText?: string;
}

const STORAGE_KEY = "qp.workingPolicy";
const listeners = new Set<() => void>();
let cache: WorkingPolicy | null = readStorage();

function stringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.length > 0);
  return items.length > 0 ? items : undefined;
}

function readStorage(): WorkingPolicy | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkingPolicy>;
    if (!parsed.policyId) return null;
    return {
      ...parsed,
      policyId: parsed.policyId,
      version: parsed.version ?? 1,
      confirmed: Boolean(parsed.confirmed),
      market: parsed.market ?? "KR_STOCK",
      riskProfile: parsed.riskProfile ?? "moderate",
      executionMode: parsed.executionMode ?? "approval_required",
      broker: parsed.broker ?? "mock",
      symbols: stringList(parsed.symbols),
      sectors: stringList(parsed.sectors),
      themes: stringList(parsed.themes),
      savedAt: parsed.savedAt ?? new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

function emit() {
  for (const listener of listeners) listener();
}

export function setWorkingPolicy(
  policy: UserPolicy,
  confirmed: boolean,
  metadata: WorkingPolicyMetadata = {},
) {
  const previousId = cache?.policyId;
  cache = {
    policyId: policy.policy_id,
    version: policy.version ?? 1,
    confirmed,
    market: policy.market,
    riskProfile: policy.risk_profile,
    executionMode: policy.execution_mode,
    broker: policy.broker,
    direction: metadata.direction,
    symbols: stringList(metadata.symbols) ?? stringList(policy.preferred_symbols),
    sectors: stringList(metadata.sectors) ?? stringList(policy.preferred_sectors),
    themes: stringList(metadata.themes) ?? stringList(policy.preferred_themes),
    generatedPolicyText: metadata.generatedPolicyText,
    savedAt: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  if (previousId && previousId !== policy.policy_id) {
    clearRunSnapshot();
  }
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
