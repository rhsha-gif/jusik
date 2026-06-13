import type { ReactNode } from "react";
import { AlertTriangle, PlugZap, RefreshCw, SearchX } from "lucide-react";
import { ApiError, getApiBase } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { JsonViewer, CopyButton } from "@/components/json-viewer";
import { Card, CardContent } from "@/components/ui/card";

const BACKEND_COMMAND =
  "python -m uvicorn quantpilot.services.api.main:app --reload --port 8010";

export function OfflineState({ onRetry }: { onRetry?: () => void }) {
  return (
    <Card role="alert">
      <CardContent className="flex flex-col items-start gap-4 p-8">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-full bg-warn-soft text-warn">
            <PlugZap className="size-5" />
          </span>
          <div>
            <h3 className="text-[15px] font-semibold">백엔드에 연결할 수 없습니다</h3>
            <p className="text-[13px] text-muted">
              {getApiBase()} 에서 응답이 없습니다. 로컬 백엔드를 먼저 실행해 주세요.
            </p>
          </div>
        </div>
        <div className="flex w-full items-center justify-between gap-3 rounded-xl bg-surface-solid border border-hairline px-4 py-3">
          <code className="font-mono text-[12.5px] text-ink break-all">{BACKEND_COMMAND}</code>
          <CopyButton text={BACKEND_COMMAND} label="명령 복사" />
        </div>
        {onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            <RefreshCw /> 다시 연결
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

export function ErrorState({
  error,
  onRetry,
  context,
}: {
  error: unknown;
  onRetry?: () => void;
  context?: string;
}) {
  if (error instanceof ApiError && error.isOffline) {
    return <OfflineState onRetry={onRetry} />;
  }
  const status = error instanceof ApiError ? error.status : null;
  const path = error instanceof ApiError ? error.path : null;
  const body = error instanceof ApiError ? error.body : null;
  const message = error instanceof Error ? error.message : String(error);

  return (
    <Card role="alert">
      <CardContent className="flex flex-col gap-4 p-6">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-danger-soft text-danger">
            <AlertTriangle className="size-4.5" />
          </span>
          <div className="min-w-0">
            <h3 className="text-[15px] font-semibold">
              {context ?? "요청이 실패했습니다"}
            </h3>
            <p className="mt-1 text-[13px] leading-relaxed text-muted break-words">{message}</p>
            <p className="mt-1.5 font-mono text-[12px] text-muted">
              {status !== null ? `HTTP ${status}` : "no response"}
              {path ? ` · ${path}` : ""}
            </p>
          </div>
        </div>
        {body != null && <JsonViewer data={body} title="오류 본문 (Raw)" />}
        {onRetry && (
          <div>
            <Button variant="secondary" size="sm" onClick={onRetry}>
              <RefreshCw /> 다시 시도
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-hairline px-8 py-14 text-center">
      <span className="flex size-11 items-center justify-center rounded-full bg-accent-soft text-accent">
        <SearchX className="size-5" />
      </span>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      {description && (
        <p className="max-w-sm text-[13px] leading-relaxed text-muted">{description}</p>
      )}
      {action}
    </div>
  );
}
