import { useState } from "react";
import {
  AlertTriangle,
  Ban,
  Bot,
  CheckCircle2,
  CircleSlash,
  FileText,
  Lock,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/misc";
import { JsonViewer } from "@/components/json-viewer";
import { EmptyState, ErrorState, OfflineState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import {
  useLatestOperatorReport,
  useOperatorRunOnce,
  useOperatorStatus,
} from "@/lib/queries";
import { useWorkingPolicy } from "@/lib/working-policy";
import type {
  FallbackDecision,
  OperatorDecision,
  OperatorRunMode,
  OperatorRunRequest,
  OperatorRunResult,
  OperatorRunStatus,
  OperatorStatusResponse,
} from "@/lib/types";
import { formatTime } from "@/lib/utils";

const RUN_MODE_META: Record<
  OperatorRunMode,
  { label: string; hint: string; submission: boolean }
> = {
  dry_run: {
    label: "Dry Run (감사 전용, 제출 없음)",
    hint: "주문을 제출하지 않고 결정·리포트만 생성합니다. 기본값이며 가장 안전합니다.",
    submission: false,
  },
  mock_submit: {
    label: "Mock Submit (모의 브로커 한정)",
    hint: "모의 브로커에만 제출합니다. 실제 자본·실거래는 일절 관여하지 않습니다.",
    submission: true,
  },
  paper_submit: {
    label: "Paper Submit (페이퍼 트레이딩 한정)",
    hint: "페이퍼 트레이딩(모의 자본)에만 제출합니다. 실거래가 아닙니다.",
    submission: true,
  },
};

const RUN_STATUS_VARIANT: Record<OperatorRunStatus, "safe" | "warn" | "danger"> = {
  completed: "safe",
  blocked: "warn",
  fallback: "warn",
  failed: "danger",
};

const DECISION_VARIANT: Record<
  OperatorDecision["action"],
  "accent" | "danger" | "warn" | "neutral"
> = {
  submit: "accent",
  block: "danger",
  fallback: "warn",
  noop: "neutral",
};

export function OperatorPage() {
  const status = useOperatorStatus();
  const latestReport = useLatestOperatorReport();
  const run = useOperatorRunOnce();
  const workingPolicy = useWorkingPolicy();

  const [policyId, setPolicyId] = useState(workingPolicy?.policyId ?? "");
  const [policyVersion, setPolicyVersion] = useState("1");
  const [runMode, setRunMode] = useState<OperatorRunMode>("dry_run");

  const submission = RUN_MODE_META[runMode].submission;
  const canSubmit = policyId.trim().length > 0 && !run.isPending;

  const submit = () => {
    const request: OperatorRunRequest = {
      user_id: "fixture-user",
      policy_id: policyId.trim(),
      requested_policy_version: Number.parseInt(policyVersion, 10) || 0,
      run_mode: runMode,
      requested_at: new Date().toISOString(),
      idempotency_key: crypto.randomUUID(),
    };
    run.mutate(request);
  };

  return (
    <>
      <PageHeader
        title="Level 5 운영자 (Operator)"
        description="완전 자동 운영자 하니스입니다. Level 5는 실거래가 아닙니다 — 실행은 기본 dry_run이며, 제출 모드도 모의/페이퍼 브로커에만 적용됩니다. 백엔드 안전 게이트가 모두 통과되어야만 진행됩니다."
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void status.refetch()}
            disabled={status.isFetching}
          >
            <RefreshCw className={status.isFetching ? "animate-spin" : ""} />
            새로고침
          </Button>
        }
      />

      {status.isError ? (
        status.error && isOffline(status.error) ? (
          <OfflineState onRetry={() => void status.refetch()} />
        ) : (
          <ErrorState
            error={status.error}
            context="운영자 상태를 불러오지 못했습니다"
            onRetry={() => void status.refetch()}
          />
        )
      ) : (
        <OperatorStatusCard status={status.data} pending={status.isPending} />
      )}

      <RunOnceCard
        policyId={policyId}
        onPolicyId={setPolicyId}
        policyVersion={policyVersion}
        onPolicyVersion={setPolicyVersion}
        runMode={runMode}
        onRunMode={setRunMode}
        submission={submission}
        canSubmit={canSubmit}
        pending={run.isPending}
        onSubmit={submit}
        workingPolicyId={workingPolicy?.policyId ?? null}
      />

      {run.isError && (
        <ErrorState
          error={run.error}
          context="운영자 1회 실행에 실패했습니다"
          onRetry={canSubmit ? submit : undefined}
        />
      )}

      {run.isSuccess && <RunResultCard result={run.data} />}

      <LatestReportCard
        text={latestReport.data?.text ?? ""}
        report={latestReport.data?.report ?? null}
        pending={latestReport.isPending}
        isError={latestReport.isError}
      />
    </>
  );
}

function isOffline(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "isOffline" in error &&
    Boolean((error as { isOffline: unknown }).isOffline)
  );
}

function flagVariant(key: string, value: boolean | string): "safe" | "warn" | "danger" | "neutral" {
  if (typeof value === "string") return "neutral";
  if (!value) return "safe";
  return key.toUpperCase().includes("LIVE_TRADING") ? "danger" : "warn";
}

function OperatorStatusCard({
  status,
  pending,
}: {
  status: OperatorStatusResponse | undefined;
  pending: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4.5 text-safe" /> 운영자 안전 상태
        </CardTitle>
        <CardDescription>GET /api/operator/status — 기능 플래그, 브로커 안전 상태, 전략 레지스트리</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {pending || !status ? (
          <Skeleton className="h-24" />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={status.live_trading_enabled ? "danger" : "safe"}>
                live_trading_enabled: {String(status.live_trading_enabled)}
              </Badge>
              {Object.entries(status.feature_flags).map(([key, value]) => (
                <Badge key={key} variant={flagVariant(key, value)}>
                  {key}: {String(value)}
                </Badge>
              ))}
              <Badge variant="neutral">runs: {status.runs}</Badge>
            </div>

            <RegistryTable entries={status.registry} />

            <JsonViewer data={status} title="Raw JSON (operator status)" />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RegistryTable({ entries }: { entries: OperatorStatusResponse["registry"] }) {
  if (entries.length === 0) {
    return <p className="text-[13px] text-muted">등록된 전략이 없습니다.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-hairline text-[12px] text-muted">
            <th className="py-2.5 pr-4 font-medium">전략</th>
            <th className="py-2.5 pr-4 font-medium">버전</th>
            <th className="py-2.5 pr-4 font-medium">상태</th>
            <th className="py-2.5 pr-4 font-medium">허용 실행 레벨</th>
            <th className="py-2.5 font-medium">비활성 사유</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={`${entry.strategy_id}@${entry.version}`} className="h-11 border-b border-hairline/60">
              <td className="pr-4 font-mono font-semibold">{entry.strategy_id}</td>
              <td className="pr-4 font-mono">{entry.version}</td>
              <td className="pr-4">
                <Badge variant={entry.status === "disabled" || entry.status === "revoked" ? "warn" : "neutral"}>
                  {entry.status}
                </Badge>
              </td>
              <td className="pr-4 text-[12px] text-muted">
                {entry.allowed_execution_levels.join(", ") || "—"}
              </td>
              <td className="text-[12px] text-muted">{entry.disabled_reason ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunOnceCard({
  policyId,
  onPolicyId,
  policyVersion,
  onPolicyVersion,
  runMode,
  onRunMode,
  submission,
  canSubmit,
  pending,
  onSubmit,
  workingPolicyId,
}: {
  policyId: string;
  onPolicyId: (value: string) => void;
  policyVersion: string;
  onPolicyVersion: (value: string) => void;
  runMode: OperatorRunMode;
  onRunMode: (value: OperatorRunMode) => void;
  submission: boolean;
  canSubmit: boolean;
  pending: boolean;
  onSubmit: () => void;
  workingPolicyId: string | null;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4.5 text-accent" /> 운영자 1회 실행
          </CardTitle>
          <CardDescription>
            POST /api/operator/run-once — 정책·버전·실행 모드를 지정해 단일 운영자 사이클을 실행합니다. 멱등 키는 매 실행마다 자동 생성됩니다.
          </CardDescription>
        </div>
        <Button onClick={onSubmit} disabled={!canSubmit}>
          <PlayCircle /> {pending ? "실행 중…" : "1회 실행"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="op-policy-id">정책 ID (policy_id)</Label>
            <Input
              id="op-policy-id"
              value={policyId}
              onChange={(event) => onPolicyId(event.target.value)}
              placeholder="policy_..."
            />
            {workingPolicyId && (
              <p className="text-[11.5px] text-muted">
                작업 정책에서 가져옴: <code className="font-mono">{workingPolicyId}</code>
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="op-policy-version">요청 정책 버전 (requested_policy_version)</Label>
            <Input
              id="op-policy-version"
              type="number"
              min={0}
              value={policyVersion}
              onChange={(event) => onPolicyVersion(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="op-run-mode">실행 모드 (run_mode)</Label>
            <Select value={runMode} onValueChange={(value) => onRunMode(value as OperatorRunMode)}>
              <SelectTrigger id="op-run-mode" aria-label="실행 모드">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(RUN_MODE_META) as OperatorRunMode[]).map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {RUN_MODE_META[mode].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11.5px] text-muted">{RUN_MODE_META[runMode].hint}</p>
          </div>
        </div>

        {submission ? (
          <div
            role="note"
            className="flex items-start gap-2.5 rounded-xl border border-warn/40 bg-warn-soft px-3.5 py-3 text-[12.5px] text-warn"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              제출 모드가 선택되었습니다. <strong>모의/페이퍼 브로커에만</strong> 제출되며 실제 자본·실거래는 관여하지 않습니다. 백엔드가 브로커 모드 불일치를 감지하면 자동으로 하위 레벨로 폴백됩니다.
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-[12.5px] text-safe">
            <Lock className="size-4 shrink-0" />
            <span>Dry Run: 주문이 제출되지 않습니다. 결정과 리포트만 기록됩니다.</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RunResultCard({ result }: { result: OperatorRunResult }) {
  const report = result.report;
  const selection = report.strategy_selection;
  const rejected = Object.entries(selection.rejected);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CheckCircle2 className="size-4.5 text-accent" /> 실행 결과
        </CardTitle>
        <CardDescription>
          <code className="font-mono">{result.run_id}</code> · {formatTime(report.completed_at)}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={RUN_STATUS_VARIANT[result.status]}>status: {result.status}</Badge>
          <Badge variant={report.live_trading_enabled ? "danger" : "safe"}>
            live_trading_enabled: {String(report.live_trading_enabled)}
          </Badge>
          <Badge variant="neutral">policy v{report.policy_version}</Badge>
          <Badge variant="neutral">audit_event_count: {report.audit_event_count}</Badge>
        </div>

        {result.fallback && <FallbackBanner fallback={result.fallback} />}

        <div className="grid gap-3 sm:grid-cols-2">
          <IdList
            title="제출된 주문 플랜"
            ids={result.submitted_order_plan_ids}
            tone="safe"
          />
          <IdList title="차단된 주문 플랜" ids={result.blocked_order_plan_ids} tone="warn" />
          <IdList title="브로커 주문 ID" ids={report.broker_order_ids} tone="neutral" />
          <IdList title="리스크 체크 ID" ids={report.risk_check_ids} tone="neutral" />
        </div>

        <div className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-3">
          <p className="text-[12px] text-muted">전략 선택 (strategy_selection)</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {selection.selected_strategy_id ? (
              <Badge variant="accent">
                선택됨: {selection.selected_strategy_id}
                {selection.selected_version ? ` @${selection.selected_version}` : ""}
              </Badge>
            ) : (
              <Badge variant="warn">선택된 전략 없음</Badge>
            )}
            <span className="text-[12.5px] text-muted">{selection.reason}</span>
          </div>
          {rejected.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1">
              {rejected.map(([id, reason]) => (
                <li key={id} className="flex items-center gap-2 text-[12px] text-muted">
                  <CircleSlash className="size-3.5 shrink-0 text-warn" />
                  <code className="font-mono">{id}</code>
                  <span>— {reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DecisionsTable decisions={report.decisions} />

        <SafetyFlags flags={report.safety_flags} />

        <JsonViewer data={result} title="Raw JSON (run result)" />
      </CardContent>
    </Card>
  );
}

function FallbackBanner({ fallback }: { fallback: FallbackDecision }) {
  return (
    <div
      role="note"
      className="flex items-start gap-2.5 rounded-xl border border-warn/40 bg-warn-soft px-3.5 py-3 text-[12.5px] text-warn"
    >
      <Ban className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-medium">
          폴백: Level {fallback.from_level} → Level {fallback.to_level} ·{" "}
          <code className="font-mono">{fallback.reason_code}</code>
        </p>
        <p className="mt-0.5 text-warn/90">{fallback.detail}</p>
        <p className="mt-0.5 text-[11.5px]">
          order_submission_enabled: {String(fallback.order_submission_enabled)}
        </p>
      </div>
    </div>
  );
}

function IdList({
  title,
  ids,
  tone,
}: {
  title: string;
  ids: string[];
  tone: "safe" | "warn" | "neutral";
}) {
  return (
    <div className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-3">
      <div className="flex items-center justify-between">
        <p className="text-[12px] text-muted">{title}</p>
        <Badge variant={ids.length > 0 ? tone : "neutral"}>{ids.length}</Badge>
      </div>
      {ids.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-0.5">
          {ids.map((id) => (
            <li key={id} className="font-mono text-[12px] text-ink break-all">
              {id}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DecisionsTable({ decisions }: { decisions: OperatorDecision[] }) {
  if (decisions.length === 0) {
    return <p className="text-[13px] text-muted">기록된 결정이 없습니다.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[600px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-hairline text-[12px] text-muted">
            <th className="py-2.5 pr-4 font-medium">액션</th>
            <th className="py-2.5 pr-4 font-medium">전략 / 주문 플랜</th>
            <th className="py-2.5 pr-4 font-medium">사유</th>
            <th className="py-2.5 font-medium">리스크 체크</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((decision) => (
            <tr key={decision.decision_id} className="border-b border-hairline/60 align-top">
              <td className="py-2.5 pr-4">
                <Badge variant={DECISION_VARIANT[decision.action]}>{decision.action}</Badge>
              </td>
              <td className="py-2.5 pr-4 font-mono text-[12px] text-muted">
                {decision.strategy_id ?? "—"}
                {decision.order_plan_id ? ` / ${decision.order_plan_id}` : ""}
              </td>
              <td className="py-2.5 pr-4 text-[12.5px]">{decision.reason}</td>
              <td className="py-2.5 font-mono text-[12px] text-muted">
                {decision.risk_check_id ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SafetyFlags({ flags }: { flags: Record<string, boolean | string> }) {
  const entries = Object.entries(flags);
  if (entries.length === 0) return null;
  return (
    <div className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-3">
      <p className="text-[12px] text-muted">안전 플래그 (safety_flags)</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        {entries.map(([key, value]) => (
          <Badge key={key} variant={flagVariant(key, value)}>
            {key}: {String(value)}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function LatestReportCard({
  text,
  report,
  pending,
  isError,
}: {
  text: string;
  report: OperatorRunResult["report"] | null;
  pending: boolean;
  isError: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="size-4.5 text-muted" /> 최신 운영자 리포트
        </CardTitle>
        <CardDescription>GET /api/operator/reports/latest — 결정론적 텍스트 렌더링과 전체 리포트</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {pending ? (
          <Skeleton className="h-24" />
        ) : isError ? (
          <p role="alert" className="text-[13px] text-danger">
            최신 리포트를 불러오지 못했습니다.
          </p>
        ) : report ? (
          <>
            <pre className="max-h-96 overflow-auto rounded-xl border border-hairline bg-surface-solid px-3.5 py-3 font-mono text-[12px] leading-relaxed text-ink whitespace-pre-wrap">
              {text || "(텍스트 렌더링 없음)"}
            </pre>
            <JsonViewer data={report} title="Raw JSON (latest report)" />
          </>
        ) : (
          <EmptyState
            title="아직 운영자 실행 기록이 없습니다"
            description="위에서 1회 실행을 수행하면 최신 리포트가 여기에 표시됩니다."
          />
        )}
      </CardContent>
    </Card>
  );
}
