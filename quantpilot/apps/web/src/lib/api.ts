import { recordActivity } from "./activity-log";

const DEFAULT_API_BASE = "http://127.0.0.1:8010";
const API_BASE_STORAGE_KEY = "qp.apiBase";

export function getApiBase(): string {
  if (typeof localStorage !== "undefined") {
    const stored = localStorage.getItem(API_BASE_STORAGE_KEY);
    if (stored) return stored.replace(/\/$/, "");
  }
  const env = import.meta.env?.VITE_API_BASE_URL as string | undefined;
  return (env ?? DEFAULT_API_BASE).replace(/\/$/, "");
}

export function setApiBase(url: string) {
  localStorage.setItem(API_BASE_STORAGE_KEY, url.replace(/\/$/, ""));
}

export function resetApiBase() {
  localStorage.removeItem(API_BASE_STORAGE_KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
    public readonly path: string,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isOffline(): boolean {
    return this.status === null;
  }
}

function extractDetailMessage(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "error" in detail) {
      return String((detail as { error: unknown }).error);
    }
    return JSON.stringify(detail);
  }
  return null;
}

export interface ApiFetchOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const base = getApiBase();
  const startedAt = new Date().toISOString();
  const start = performance.now();

  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    const durationMs = performance.now() - start;
    const message = `백엔드(${base})에 연결할 수 없습니다.`;
    recordActivity({
      id: crypto.randomUUID(),
      method,
      path,
      requestBody: options.body ?? null,
      responseBody: null,
      status: null,
      ok: false,
      errorMessage: error instanceof Error ? error.message : String(error),
      startedAt,
      durationMs,
    });
    throw new ApiError(message, null, path, null);
  }

  const durationMs = performance.now() - start;
  let responseBody: unknown = null;
  try {
    responseBody = await response.json();
  } catch {
    responseBody = null;
  }

  recordActivity({
    id: crypto.randomUUID(),
    method,
    path,
    requestBody: options.body ?? null,
    responseBody,
    status: response.status,
    ok: response.ok,
    errorMessage: response.ok
      ? null
      : extractDetailMessage(responseBody) ?? response.statusText,
    startedAt,
    durationMs,
  });

  if (!response.ok) {
    const detail = extractDetailMessage(responseBody);
    throw new ApiError(
      detail ?? `요청이 실패했습니다 (HTTP ${response.status})`,
      response.status,
      path,
      responseBody,
    );
  }

  return responseBody as T;
}
