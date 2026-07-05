import {
  Ban,
  Bot,
  CheckCircle2,
  CircleDashed,
  Coins,
  FileBarChart,
  FlaskConical,
  ListChecks,
  PlayCircle,
  Radar,
  Scale,
  ScrollText,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Stat } from "@/components/ui/stat";
import { WeightsBars } from "@/components/charts";
import { JsonViewer } from "@/components/json-viewer";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useHealth, useLevel12MockExecute, useLevel12Run } from "@/lib/queries";
import { useWorkingPolicy } from "@/lib/working-policy";
import type {
  AutoExecutionDecision,
  AutoExecutionSummary,
  Level12MockExecutionResponse,
  Level12RunResponse,
  RebalanceSuggestion,
  SignalActionValue,
} from "@/lib/types";
import { cn, formatKRW, formatPercent } from "@/lib/utils";

const ACTION_LABEL: Record<SignalActionValue, string> = {
  buy_ready: "매수 준비",
  buy_wait: "매수 대기",
  hold: "보유",
  trim: "축소",
  exit: "정리",
  watch: "관찰",
  blocked: "차단",
};

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
  const mockExecute = useLevel12MockExecute();

  const backendOk = health.isSuccess && health.data.status === "ok";
  const mockMode = health.isSuccess && health.data.default_broker === "mock";

  const requestBody = () =>
    workingPolicy ? { policy_id: workingPolicy.policyId } : {};

  return (
    <>
      <PageHeader
        eyebrow="Level 1-2 · 프로그램 자동매매"
        title="Level 1-2 자동매매"
        description="모의 단계에서는 프로그램이 매매 타이밍을 스스로 판단하고 모의 계좌(MockBroker)에 자동으로 주문을 제출합니다. 사람이 일일이 승인하지 않습니다. 실거래는 별도의 승인 알림 단계를 거칩니다."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="lg"
              onClick={() => mockExecute.mutate(requestBody())}
              disabled={run.isPending || mockExecute.isPending || !backendOk || !mockMode}
            >
              <Bot /> {mockExecute.isPending ? "자동매매 중…" : "모의 자동매매 실행"}
            </Button>
            <Button
              variant="secondary"
              size="lg"
              onClick={() => run.mutate(requestBody())}
              disabled={run.isPending || mockExecute.isPending || !backendOk}
            >
              <ListChecks /> {run.isPending ? "실행 중…" : "제안만 보기 (체결 안 함)"}
            </Button>
          </div>
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
            <PreflightItem ok={true} label="실거래 차단 · 모의 계좌만 자동 체결" />
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
      {mockExecute.isError && (
        <ErrorState
          error={mockExecute.error}
          context="Level 1-2 모의체결에 실패했습니다"
          onRetry={() => mockExecute.mutate(requestBody())}
        />
      )}

      {mockExecute.isSuccess && <MockExecutionResult result={mockExecute.data} />}
      {run.isSuccess && <RunResult result={run.data} />}
      {(run.isPending || mockExecute.isPending) && <RunTimeline activeIndex={2} failed={false} />}
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
        <Stat
          label="후보 종목"
          value={summary.candidate_count ?? result.universe.length}
          icon={FlaskConical}
        />
        <Stat
          label="애널리스트 리포트"
          value={summary.analyst_report_count ?? result.analyst_reports.length}
          icon={FileBarChart}
        />
        <Stat
          label="신호"
          value={summary.signal_count ?? result.signals.length}
          icon={Radar}
          tone="accent"
        />
        <Stat
          label="리밸런스 제안"
          value={summary.rebalance_suggestion_count ?? result.rebalance.suggestions.length}
          icon={Scale}
        />
        <Stat label="감사 이벤트" value={result.daily_report.audit_event_count} icon={ScrollText} />
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
        <CardContent className="flex flex-col gap-5">
          {result.rebalance.suggestions.length === 0 ? (
            <p className="text-[13px] text-muted">생성된 제안이 없습니다.</p>
          ) : (
            <>
              {/* Visual summary only — the table below is the accessible source. */}
              <div aria-hidden>
                <div className="mb-3 flex items-center gap-4 text-[12px] text-muted">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full bg-faint" /> 현재 비중
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full bg-accent" /> 목표 비중 (제안)
                  </span>
                </div>
                <WeightsBars
                  data={result.rebalance.suggestions.map((item) => ({
                    label: item.ticker,
                    current: item.current_weight,
                    target: item.target_weight_suggestion,
                  }))}
                />
              </div>
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
                        <tr
                          key={item.ticker}
                          className="h-12 border-b border-hairline/60 transition-colors hover:bg-surface-solid/70"
                        >
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
            </>
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

function MockExecutionResult({ result }: { result: Level12MockExecutionResponse }) {
  return (
    <div className="flex flex-col gap-5">
      <AutoExecutionPanel summary={result.auto_execution} />
      <Card>
      <CardHeader>
        <CardTitle>상세 체결 내역</CardTitle>
        <CardDescription>
          MockBroker에만 제출된 결과입니다. KIS 모의투자와 실거래 브로커는 아직 연결하지 않습니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="safe">live_trading_enabled: {String(result.live_trading_enabled)}</Badge>
          <Badge variant="safe">broker: {result.broker}</Badge>
          <Badge variant="neutral">data_mode: {result.data_mode}</Badge>
          <Badge variant={result.order_submission_enabled ? "warn" : "neutral"}>
            order_submission_enabled: {String(result.order_submission_enabled)}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="제안 주문" value={result.proposals.length} icon={ListChecks} />
          <Stat label="제출 주문" value={result.submitted_order_plans.length} icon={PlayCircle} tone="accent" />
          <Stat label="브로커 주문" value={result.broker_orders.length} icon={Scale} />
          <Stat label="체결" value={result.fills.length} icon={CheckCircle2} tone="safe" />
        </div>

        {result.submitted_order_plans.length === 0 ? (
          <p className="text-[13px] text-muted">제출된 mock 주문이 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-hairline text-[12px] text-muted">
                  <th className="py-2.5 pr-4 font-medium">주문 계획</th>
                  <th className="py-2.5 pr-4 font-medium">종목</th>
                  <th className="py-2.5 pr-4 font-medium">방향</th>
                  <th className="py-2.5 pr-4 font-medium">금액</th>
                  <th className="py-2.5 pr-4 font-medium">상태</th>
                  <th className="py-2.5 font-medium">브로커 주문</th>
                </tr>
              </thead>
              <tbody>
                {result.submitted_order_plans.map((order) => {
                  const brokerOrder = result.broker_orders.find((item) => item.order_plan_id === order.order_plan_id);
                  return (
                    <tr
                      key={order.order_plan_id}
                      className="h-12 border-b border-hairline/60 transition-colors hover:bg-surface-solid/70"
                    >
                      <td className="pr-4 font-mono text-[12px]">{order.order_plan_id}</td>
                      <td className="pr-4 font-mono font-semibold">{order.intent.symbol}</td>
                      <td className="pr-4">
                        <Badge variant={order.intent.side === "buy" ? "safe" : "warn"}>
                          {order.intent.side}
                        </Badge>
                      </td>
                      <td className="pr-4 tabular-nums">{formatKRW(order.intent.notional)}</td>
                      <td className="pr-4">
                        <Badge variant={order.status === "filled" ? "safe" : "neutral"}>{order.status}</Badge>
                      </td>
                      <td className="font-mono text-[12px] text-muted">
                        {brokerOrder?.broker_order_id ?? "-"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {result.blocked_proposals.length > 0 && (
          <div className="rounded-xl border border-warn/40 bg-warn-soft px-3.5 py-3 text-[12.5px] text-warn">
            차단된 제안 {result.blocked_proposals.length}건:{" "}
            {result.blocked_proposals.map((item) => `${item.order_plan_id}(${item.reason})`).join(", ")}
          </div>
        )}

        <JsonViewer data={result} title="Raw JSON (mock execution)" />
      </CardContent>
      </Card>
    </div>
  );
}

/**
 * The hero of a Level 1-2 auto-trade run: makes the "program judged the timing
 * and traded for you" narrative explicit, with a per-symbol decision table.
 * `executed` decisions were filled on the mock account; `blocked` decisions were
 * stopped by a risk/guardrail gate.
 */
function AutoExecutionPanel({ summary }: { summary: AutoExecutionSummary }) {
  const blockedTone = summary.blocked > 0 ? "warn" : "default";
  return (
    <Card className="border-accent/30 bg-accent-soft/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4.5 text-accent" /> 프로그램 자동 매매 판단
        </CardTitle>
        <CardDescription>
          신호에서 매매 타이밍을 스스로 판단하고 모의 계좌에 자동으로 주문을 제출했습니다 — 사람의 승인 없이
          프로그램이 직접 체결합니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={summary.executed ? "safe" : "neutral"}>
            {summary.executed ? "자동 체결됨" : "체결된 주문 없음"}
          </Badge>
          <Badge variant="neutral">mode: {summary.mode}</Badge>
          <Badge variant="safe">live_trading_enabled: {String(summary.live_trading_enabled)}</Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="타이밍 평가 신호" value={summary.signals_evaluated} icon={Radar} />
          <Stat label="자동 체결 주문" value={summary.auto_submitted} icon={PlayCircle} tone="accent" />
          <Stat label="체결 금액" value={formatKRW(summary.filled_notional)} icon={Coins} tone="safe" />
          <Stat label="차단" value={summary.blocked} icon={Ban} tone={blockedTone} />
        </div>

        {summary.decisions.length === 0 ? (
          <p className="text-[13px] text-muted">
            이번 실행에서는 프로그램이 매매할 타이밍이라고 판단한 종목이 없습니다.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-hairline text-[12px] text-muted">
                  <th className="py-2.5 pr-4 font-medium">종목</th>
                  <th className="py-2.5 pr-4 font-medium">신호</th>
                  <th className="py-2.5 pr-4 font-medium">방향</th>
                  <th className="py-2.5 pr-4 font-medium">수량 · 금액</th>
                  <th className="py-2.5 pr-4 font-medium">프로그램 결정</th>
                  <th className="py-2.5 font-medium">판단 근거</th>
                </tr>
              </thead>
              <tbody>
                {summary.decisions.map((decision) => (
                  <DecisionRow key={decision.order_plan_id} decision={decision} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DecisionRow({ decision }: { decision: AutoExecutionDecision }) {
  const executed = decision.decision === "executed";
  return (
    <tr className="h-12 border-b border-hairline/60 transition-colors hover:bg-surface-solid/70">
      <td className="pr-4 font-mono font-semibold">{decision.symbol}</td>
      <td className="pr-4 text-[12.5px] text-muted">
        {decision.action ? ACTION_LABEL[decision.action] : "—"}
        {decision.strength != null && (
          <span className="ml-1 tabular-nums text-faint">{formatPercent(decision.strength, 0)}</span>
        )}
      </td>
      <td className="pr-4">
        <Badge variant={decision.side === "buy" ? "safe" : "warn"}>
          {decision.side === "buy" ? "매수" : "매도"}
        </Badge>
      </td>
      <td className="pr-4 tabular-nums">
        {decision.quantity.toLocaleString()}주 · {formatKRW(decision.notional)}
      </td>
      <td className="pr-4">
        <Badge variant={executed ? "safe" : "warn"}>
          {executed ? "자동 체결" : "차단"}
        </Badge>
      </td>
      <td className="text-[12.5px] text-muted">
        {executed ? decision.reason : decision.blocked_reason ?? decision.reason}
      </td>
    </tr>
  );
}
