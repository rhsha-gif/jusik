import { CheckCircle2, CircleDashed, PlayCircle, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonViewer } from "@/components/json-viewer";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useHealth, useLevel12Run } from "@/lib/queries";
import { useWorkingPolicy } from "@/lib/working-policy";
import type { Level12RunResponse, RebalanceSuggestion } from "@/lib/types";
import { cn, formatPercent } from "@/lib/utils";

const SUGGESTED_ACTION_META: Record<
  RebalanceSuggestion["suggested_action"],
  { label: string; variant: "safe" | "warn" | "neutral" | "danger" }
> = {
  buy: { label: "모의 매수 제안", variant: "safe" },
  sell: { label: "모의 매도 제안", variant: "warn" },
  hold: { label: "유지", variant: "neutral" },
  blocked: { label: "차단", variant: "danger" },
};

export function RunPage() {
  const health = useHealth();
  const workingPolicy = useWorkingPolicy();
  const run = useLevel12Run();

  const backendOk = health.isSuccess && health.data.status === "ok";
  const mockMode = health.isSuccess && health.data.default_broker === "mock";

  const requestBody = () =>
    workingPolicy ? { policy_id: workingPolicy.policyId } : {};

  return (
    <>
      <PageHeader
        eyebrow="Level 1-2"
        title="Level 1-2 실행"
        description="유니버스 → 지표 → 신호 → 리밸런스 제안 → 일일 리포트까지 모의 파이프라인 전체를 실행합니다. 주문 제출은 비활성화되어 있습니다."
        actions={
          <Button
            size="lg"
            onClick={() => run.mutate(requestBody())}
            disabled={run.isPending || !backendOk}
          >
            <PlayCircle /> {run.isPending ? "실행 중…" : "모의 Level 1-2 실행"}
          </Button>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>사전 점검 (Pre-flight)</CardTitle>
          <CardDescription>실행 전 자동으로 확인되는 조건들입니다.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
            <PreflightItem ok={backendOk} pending={health.isPending} label="백엔드 연결" />
            <PreflightItem ok={mockMode} pending={health.isPending} label="모의 브로커 모드" />
            <PreflightItem
              ok={workingPolicy != null}
              warnOnly
              label={workingPolicy ? "작업 정책 선택됨" : "작업 정책 없음 (최근/기본 정책 사용)"}
            />
            <PreflightItem ok={true} label="주문 제출 비활성 (안전)" />
          </ul>
        </CardContent>
      </Card>

      {run.isError && (
        <ErrorState
          error={run.error}
          context="Level 1-2 실행에 실패했습니다"
          onRetry={() => run.mutate(requestBody())}
        />
      )}

      {run.isSuccess && <RunResult result={run.data} />}
      {run.isPending && <RunTimeline activeIndex={2} failed={false} />}
    </>
  );
}

function PreflightItem({
  ok,
  pending = false,
  warnOnly = false,
  label,
}: {
  ok: boolean;
  pending?: boolean;
  warnOnly?: boolean;
  label: string;
}) {
  return (
    <li className="flex items-center gap-2.5 rounded-xl border border-hairline bg-surface-raised px-3.5 py-3 text-[13px] shadow-sm">
      {pending ? (
        <CircleDashed className="size-4 shrink-0 animate-spin text-muted" />
      ) : ok ? (
        <CheckCircle2 className="size-4 shrink-0 text-safe" />
      ) : warnOnly ? (
        <CircleDashed className="size-4 shrink-0 text-warn" />
      ) : (
        <XCircle className="size-4 shrink-0 text-danger" />
      )}
      <span className={cn(!ok && !warnOnly && !pending && "text-danger")}>{label}</span>
    </li>
  );
}

const TIMELINE_STEPS = ["제출", "정책 검증", "리서치", "신호", "리밸런스 제안", "완료"];

function RunTimeline({ activeIndex, failed }: { activeIndex: number; failed: boolean }) {
  return (
    <ol aria-label="실행 단계" className="flex flex-wrap items-center gap-2">
      {TIMELINE_STEPS.map((step, index) => {
        const done = index < activeIndex || (!failed && activeIndex >= TIMELINE_STEPS.length - 1);
        const active = index === activeIndex && !done;
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] font-medium",
                done && "border-transparent bg-safe-soft text-safe",
                active && "border-transparent bg-accent-soft text-accent",
                !done && !active && "border-hairline text-muted",
              )}
            >
              {done ? (
                <CheckCircle2 className="size-3.5" />
              ) : active ? (
                <CircleDashed className="size-3.5 animate-spin" />
              ) : (
                <CircleDashed className="size-3.5" />
              )}
              {step}
            </span>
            {index < TIMELINE_STEPS.length - 1 && (
              <span aria-hidden className="h-px w-4 bg-hairline" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function RunResult({ result }: { result: Level12RunResponse }) {
  const summary = result.daily_report.summary;
  return (
    <div className="flex flex-col gap-5">
      <RunTimeline activeIndex={TIMELINE_STEPS.length - 1} failed={false} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <RunStat label="후보 종목" value={summary.candidate_count ?? result.universe.length} />
        <RunStat
          label="애널리스트 리포트"
          value={summary.analyst_report_count ?? result.analyst_reports.length}
        />
        <RunStat label="신호" value={summary.signal_count ?? result.signals.length} />
        <RunStat
          label="리밸런스 제안"
          value={summary.rebalance_suggestion_count ?? result.rebalance.suggestions.length}
        />
        <RunStat label="감사 이벤트" value={result.daily_report.audit_event_count} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="safe">live_trading_enabled: false</Badge>
        <Badge variant="safe">주문 제출 비활성</Badge>
        <Badge variant="neutral">broker: {String(summary.broker ?? "mock")}</Badge>
        <Badge variant="neutral">{String(summary.execution_mode ?? "")}</Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>리밸런스 제안 (모의)</CardTitle>
          <CardDescription>
            제안일 뿐 주문이 제출되지 않습니다 · order_submission_enabled:{" "}
            {String(result.rebalance.order_submission_enabled)}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {result.rebalance.suggestions.length === 0 ? (
            <p className="text-[13px] text-muted">생성된 제안이 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-[13px]">
                <thead>
                  <tr className="border-b border-hairline text-[12px] text-muted">
                    <th className="py-2.5 pr-4 font-medium">종목</th>
                    <th className="py-2.5 pr-4 font-medium">현재 비중</th>
                    <th className="py-2.5 pr-4 font-medium">목표 비중</th>
                    <th className="py-2.5 pr-4 font-medium">제안</th>
                    <th className="py-2.5 font-medium">리스크 사유</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rebalance.suggestions.map((item) => {
                    const meta = SUGGESTED_ACTION_META[item.suggested_action];
                    return (
                      <tr key={item.ticker} className="h-12 border-b border-hairline/60">
                        <td className="pr-4 font-mono font-semibold">{item.ticker}</td>
                        <td className="pr-4 tabular-nums">
                          {formatPercent(item.current_weight)}
                        </td>
                        <td className="pr-4 tabular-nums">
                          {formatPercent(item.target_weight_suggestion)}
                        </td>
                        <td className="pr-4">
                          <Badge variant={meta.variant}>{meta.label}</Badge>
                        </td>
                        <td className="text-[12.5px] text-muted">{item.risk_reason}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>일일 리포트</CardTitle>
          <CardDescription>
            <code className="font-mono">{result.daily_report.report_id}</code> ·{" "}
            {result.daily_report.created_at}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {Array.isArray(summary.supported_actions) && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[12px] text-muted">지원 액션:</span>
              {summary.supported_actions.map((action) => (
                <Badge key={String(action)} variant="neutral">
                  {String(action)}
                </Badge>
              ))}
            </div>
          )}
          <JsonViewer data={result.daily_report} title="Raw JSON (daily report)" />
        </CardContent>
      </Card>

      <JsonViewer data={result} title="Raw JSON (전체 실행 결과)" />
    </div>
  );
}

function RunStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-hairline bg-surface-raised px-4 py-3 shadow-sm">
      <p className="text-[11.5px] font-medium uppercase tracking-wide text-faint">{label}</p>
      <p className="mt-1 text-[24px] font-semibold leading-none tabular-nums tracking-tight">
        {value}
      </p>
    </div>
  );
}
