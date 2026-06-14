import { useSyncExternalStore } from "react";
import type { IntentRunResponse, Level12RunResponse, OperatorRunResult } from "./types";
import type { RiskPreset } from "./workflow-policy";

const STORAGE_KEY = "qp.runSnapshot";
const listeners = new Set<() => void>();

export interface RunSnapshot {
  policyId: string;
  policyVersion?: number;
  direction?: string;
  riskPreset?: RiskPreset;
  symbols?: string[];
  sectors?: string[];
  themes?: string[];
  generatedPolicyText?: string;
  source?: string;
  intent?: IntentRunResponse;
  level12?: Level12RunResponse;
  operator?: OperatorRunResult;
  savedAt: string;
}

export type RunSnapshotInput = Omit<RunSnapshot, "savedAt"> & { savedAt?: string };
export type RunSnapshotPatch = Partial<RunSnapshotInput> & { policyId?: string };

function isRiskPreset(value: unknown): value is RiskPreset {
  return value === "conservative" || value === "moderate" || value === "aggressive";
}

function stringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.length > 0);
  return items.length > 0 ? items : undefined;
}

function normalizeSnapshot(value: Partial<RunSnapshot> | null): RunSnapshot | null {
  if (!value || typeof value.policyId !== "string" || value.policyId.length === 0) {
    return null;
  }
  return {
    policyId: value.policyId,
    policyVersion: typeof value.policyVersion === "number" ? value.policyVersion : undefined,
    direction: typeof value.direction === "string" ? value.direction : undefined,
    riskPreset: isRiskPreset(value.riskPreset) ? value.riskPreset : undefined,
    symbols: stringList(value.symbols),
    sectors: stringList(value.sectors),
    themes: stringList(value.themes),
    generatedPolicyText: typeof value.generatedPolicyText === "string" ? value.generatedPolicyText : undefined,
    source: typeof value.source === "string" ? value.source : undefined,
    intent: value.intent,
    level12: value.level12,
    operator: value.operator,
    savedAt: typeof value.savedAt === "string" ? value.savedAt : new Date().toISOString(),
  };
}

function readStorage(): RunSnapshot | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return normalizeSnapshot(JSON.parse(raw) as Partial<RunSnapshot>);
  } catch {
    return null;
  }
}

let cache: RunSnapshot | null = readStorage();

function emit() {
  for (const listener of listeners) listener();
}

function persist(next: RunSnapshot | null) {
  cache = next;
  if (next) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
  emit();
}

export function getRunSnapshot(): RunSnapshot | null {
  return cache;
}

export function setRunSnapshot(snapshot: RunSnapshotInput): RunSnapshot {
  const next = normalizeSnapshot({ ...snapshot, savedAt: snapshot.savedAt ?? new Date().toISOString() });
  if (!next) {
    throw new Error("Run snapshot requires a policyId");
  }
  persist(next);
  return next;
}

export function patchRunSnapshot(patch: RunSnapshotPatch): RunSnapshot | null {
  const policyId = patch.policyId ?? cache?.policyId;
  if (!policyId) return null;
  const samePolicy = cache?.policyId === policyId;
  const base = samePolicy ? cache : { policyId };
  const next = normalizeSnapshot({ ...base, ...patch, policyId, savedAt: new Date().toISOString() });
  persist(next);
  return next;
}

export function clearRunSnapshot() {
  persist(null);
}

export function useRunSnapshot(): RunSnapshot | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => cache,
    () => cache,
  );
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    cache = readStorage();
    emit();
  });
}
