import { useSyncExternalStore } from "react";

/**
 * Client-side API activity log.
 *
 * The pre-harness backend is fully synchronous and has no GET /api/jobs/{id}
 * endpoint, so "Jobs & Logs" is implemented as a transcript of every request
 * the UI makes (design.md §8.4). No secrets are ever involved — the harness
 * has no credentials.
 */
export interface ActivityEntry {
  id: string;
  method: string;
  path: string;
  requestBody: unknown;
  responseBody: unknown;
  status: number | null;
  ok: boolean;
  errorMessage: string | null;
  startedAt: string;
  durationMs: number;
}

const MAX_ENTRIES = 200;

let entries: ActivityEntry[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function recordActivity(entry: ActivityEntry) {
  entries = [entry, ...entries].slice(0, MAX_ENTRIES);
  emit();
}

export function clearActivity() {
  entries = [];
  emit();
}

export function getActivityEntries(): ActivityEntry[] {
  return entries;
}

export function useActivityLog(): ActivityEntry[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => entries,
  );
}

export function buildTroubleshootingBundle(entry: ActivityEntry): string {
  return JSON.stringify(
    {
      endpoint: `${entry.method} ${entry.path}`,
      started_at: entry.startedAt,
      duration_ms: Math.round(entry.durationMs),
      response_status: entry.status,
      error_message: entry.errorMessage,
      request_body: entry.requestBody,
      response_body: entry.responseBody,
    },
    null,
    2,
  );
}
