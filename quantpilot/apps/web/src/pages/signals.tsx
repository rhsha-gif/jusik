import { useMemo, useState } from "react";
import { Gauge, Radar, Search, ShieldAlert, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Stat } from "@/components/ui/stat";
import { ChartLegend, DistributionDonut, StrengthHistogram } from "@/components/charts";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { JsonViewer, CopyButton } from "@/components/json-viewer";
import { EmptyState, ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useSignalBoard } from "@/lib/queries";
import { useWorkingPolicy } from "@/lib/working-policy";
import type { Signal, SignalActionValue } from "@/lib/types";
import { formatPercent } from "@/lib/utils";

const ACTION_META: Record<
  SignalActionValue,
  { label: string; variant: "safe" | "accent" | "neutral" | "warn" | "danger" }
> = {
  buy_ready: { label: "매수 준비", variant: "safe" },
  buy_wait: { label: "매수 대기", variant: "accent" },
  hold: { label: "보유", variant: "neutral" },
  trim: { label: "축소", variant: "warn" },
  exit: { label: "정리", variant: "warn" },
  watch: { label: "관찰", variant: "accent" },
  blocked: { label: "차단", variant: "danger" },
};

/**
 * Distinct color per action for the distribution donut + legend. Badges keep
 * their semantic 5-tone scale, but the donut needs 7 separable hues so no two
 * slices (e.g. 매수 대기 vs 관찰, 축소 vs 정리) ever render the same color.
 */
const ACTION_COLOR: Record<SignalActionValue, string> = {
  buy_ready: "var(--qp-safe)",
  buy_wait: "var(--qp-accent)",
  watch: "var(--qp-chart-teal)",
  hold: "var(--qp-faint)",
  trim: "var(--qp-warn)",
  exit: "var(--qp-accent-2)",
  blocked: "var(--qp-danger)",
};

export function SignalsPage() {
  const workingPolicy = useWorkingPolicy();
  const board = useSignalBoard();
  const [actionFilter, setActionFilter] = useState<string>("all");
  const [minStrength, setMinStrength] = useState<string>("0");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Signal | null>(null);

  const requestBody = () =>
    workingPolicy ? { policy_id: workingPolicy.policyId } : {};

  const filtered = useMemo(() => {
    const signals = board.data?.signals ?? [];
    return signals.filter((signal) => {
      if (actionFilter !== "all" && signal.action !== actionFilter) return false;
      if (signal.strength < Number(minStrength)) return false;
      if (search && !signal.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [board.data, actionFilter, minStrength, search]);

  return (
    <>
      <PageHeader
        eyebrow="Signal Board"
        title="신호 보드"
        description="신호 엔진의 출력을 방향·강도·사유와 함께 검토합니다. 모든 신호는 fixture 데이터 기반의 모의 신호입니다."
        actions={
          <Button onClick={() => board.mutate(requestBody())} disabled={board.isPending}>
            <Radar /> {board.isPending ? "생성 중…" : "신호 보드 생성"}
          </Button>
        }
      />

      {workingPolicy ? (
        <p className="text-[12.5px] text-muted">
          작업 정책 <code className="font-mono">{workingPolicy.policyId}</code> 기준으로
          실행됩니다.
        </p>
      ) : (
        <p className="text-[12.5px] text-muted">
          작업 정책이 없어 가장 최근 정책(또는 기본 정책)이 사용됩니다. 정책 스튜디오에서 먼저
          정책을 파싱하면 더 명확합니다.
        </p>
      )}

      {board.isError && (
        <ErrorState
          error={board.error}
          context="신호 보드 생성에 실패했습니다"
          onRetry={() => board.mutate(requestBody())}
        />
      )}

      {board.isSuccess && (
        <>
          {board.data.signals.length > 0 && <SignalsSummary signals={board.data.signals} />}

          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <Input
                aria-label="심볼 검색"
                placeholder="심볼 검색"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-44 pl-9"
              />
            </div>
            <Select value={actionFilter} onValueChange={setActionFilter}>
              <SelectTrigger aria-label="방향 필터" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">전체 방향</SelectItem>
                {Object.entries(ACTION_META).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={minStrength} onValueChange={setMinStrength}>
              <SelectTrigger aria-label="최소 강도" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">강도 전체</SelectItem>
                <SelectItem value="0.3">강도 ≥ 30%</SelectItem>
                <SelectItem value="0.5">강도 ≥ 50%</SelectItem>
                <SelectItem value="0.7">강도 ≥ 70%</SelectItem>
              </SelectContent>
            </Select>
            <span className="ml-auto text-[12.5px] tabular-nums text-muted">
              {filtered.length} / {board.data.signals.length}개 표시
            </span>
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              title="표시할 신호가 없습니다"
              description="필터를 완화하거나 신호 보드를 다시 생성해 보세요."
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filtered.map((signal) => (
                <SignalCard key={signal.signal_id} signal={signal} onOpen={setSelected} />
              ))}
            </div>
          )}

          <JsonViewer data={board.data} title="Raw JSON (signal board)" />
        </>
      )}

      {board.isIdle && (
        <EmptyState
          title="아직 신호 보드가 없습니다"
          description="“신호 보드 생성”을 누르면 POST /api/signals/board 가 호출되어 모의 신호가 생성됩니다."
        />
      )}

      <SignalDetailDialog signal={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function SignalsSummary({ signals }: { signals: Signal[] }) {
  const total = signals.length;
  const avgStrength =
    total > 0
      ? signals.reduce((s, sig) => s + (Number.isFinite(sig.strength) ? sig.strength : 0), 0) / total
      : 0;
  const buyReady = signals.filter((s) => s.action === "buy_ready").length;
  const blocked = signals.filter((s) => s.action === "blocked").length;

  // Action mix, ordered by the canonical ACTION_META sequence, zeros dropped.
  const distribution = (Object.keys(ACTION_META) as SignalActionValue[])
    .map((action) => ({
      name: ACTION_META[action].label,
      value: signals.filter((s) => s.action === action).length,
      color: ACTION_COLOR[action],
    }))
    .filter((d) => d.value > 0);

  return (
    <section aria-label="신호 요약" className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="총 신호" value={total} icon={Radar} tone="accent" />
        <Stat
          label="평균 강도"
          value={Math.round(avgStrength * 100)}
          suffix="%"
          icon={Gauge}
        />
        <Stat label="매수 준비" value={buyReady} icon={Sparkles} tone="safe" />
        <Stat label="차단" value={blocked} icon={ShieldAlert} tone={blocked > 0 ? "danger" : "default"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[15px]">방향 분포</CardTitle>
            <CardDescription>신호 액션별 구성 비율</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 items-center gap-4 sm:grid-cols-[150px_minmax(0,1fr)]">
            <DistributionDonut data={distribution} centerLabel="신호" />
            <ChartLegend
              items={distribution.map((d) => ({ name: d.name, color: d.color, value: d.value }))}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[15px]">강도 분포</CardTitle>
            <CardDescription>신호 강도(%) 구간별 분포</CardDescription>
          </CardHeader>
          <CardContent>
            <StrengthHistogram values={signals.map((s) => s.strength)} />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function SignalCard({ signal, onOpen }: { signal: Signal; onOpen: (s: Signal) => void }) {
  const meta = ACTION_META[signal.action];
  return (
    <button
      type="button"
      onClick={() => onOpen(signal)}
      className="panel group flex cursor-pointer flex-col gap-3 p-5 text-left transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:border-hairline-strong hover:shadow-lg"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[16px] font-semibold">{signal.symbol}</span>
        <Badge variant={meta.variant}>{meta.label}</Badge>
      </div>
      <div className="flex items-center gap-3">
        <div
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(signal.strength * 100)}
          aria-label={`신호 강도 ${formatPercent(signal.strength, 0)}`}
          className="h-1.5 flex-1 overflow-hidden rounded-full bg-hairline"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-2"
            style={{ width: `${Math.round(signal.strength * 100)}%` }}
          />
        </div>
        <span className="text-[12.5px] font-medium tabular-nums">
          {formatPercent(signal.strength, 0)}
        </span>
      </div>
      <p className="line-clamp-2 text-[12.5px] leading-relaxed text-muted">{signal.reason}</p>
      <div className="flex flex-wrap gap-1.5">
        {signal.reason_codes.slice(0, 3).map((code) => (
          <Badge key={code} variant="neutral">
            {code}
          </Badge>
        ))}
      </div>
    </button>
  );
}

function signalToMarkdown(signal: Signal): string {
  const meta = ACTION_META[signal.action];
  return [
    `## ${signal.symbol} — ${meta.label}`,
    "",
    `- 강도: ${formatPercent(signal.strength, 0)}`,
    `- 전략: ${signal.strategy_id} v${signal.recipe_version}`,
    `- 일자: ${signal.signal_date}`,
    `- 사유 코드: ${signal.reason_codes.join(", ") || "없음"}`,
    "",
    `> ${signal.reason}`,
    "",
    "_모의 신호 (fixture 데이터) — 실거래 비활성_",
  ].join("\n");
}

function SignalDetailDialog({
  signal,
  onClose,
}: {
  signal: Signal | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={signal != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[min(94vw,640px)]">
        {signal && (
          <>
            <DialogTitle className="flex items-center gap-3 font-mono">
              {signal.symbol}
              <Badge variant={ACTION_META[signal.action].variant}>
                {ACTION_META[signal.action].label}
              </Badge>
            </DialogTitle>
            <DialogDescription>
              {signal.strategy_id} v{signal.recipe_version} · {signal.signal_date} · 모의 신호
              (Fixture)
            </DialogDescription>
            <div className="mt-4 flex flex-col gap-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-[14px]">신호 사유</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-[13.5px] leading-relaxed">{signal.reason}</p>
                  {signal.reason_codes.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {signal.reason_codes.map((code) => (
                        <Badge key={code} variant="neutral">
                          {code}
                        </Badge>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
              <dl className="grid grid-cols-2 gap-3 text-[13px] sm:grid-cols-4">
                <DetailStat label="강도" value={formatPercent(signal.strength, 0)} />
                <DetailStat
                  label="기술 점수"
                  value={signal.technical_score != null ? String(signal.technical_score) : "—"}
                />
                <DetailStat
                  label="퀀트 점수"
                  value={signal.quant_score != null ? String(signal.quant_score) : "—"}
                />
                <DetailStat
                  label="목표 비중 힌트"
                  value={
                    signal.target_weight_hint != null
                      ? formatPercent(signal.target_weight_hint, 0)
                      : "—"
                  }
                />
              </dl>
              <div className="flex flex-wrap gap-2">
                <CopyButton text={signalToMarkdown(signal)} label="Markdown 복사" />
                <CopyButton text={JSON.stringify(signal, null, 2)} label="JSON 복사" />
              </div>
              <JsonViewer data={signal} title="Raw JSON (signal)" />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function DetailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-hairline bg-surface-raised px-3 py-2.5 shadow-sm">
      <CardDescription className="text-[11px]">{label}</CardDescription>
      <p className="mt-0.5 font-medium tabular-nums">{value}</p>
    </div>
  );
}
