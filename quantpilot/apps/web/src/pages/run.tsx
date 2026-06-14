import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, PlayCircle, ShieldCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { JsonViewer } from "@/components/json-viewer";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useHealth, useIntentRun } from "@/lib/queries";
import { setRunSnapshot } from "@/lib/run-store";
import { setWorkingPolicy, useWorkingPolicy } from "@/lib/working-policy";
import { buildSafeWorkflowPolicy, RISK_PRESETS, type RiskPreset } from "@/lib/workflow-policy";
import type { CandidateUniverseItem, IntentRunResponse, RebalanceSuggestion } from "@/lib/types";
import { cn, formatPercent } from "@/lib/utils";

const SUGGESTED_ACTION_META: Record<
  RebalanceSuggestion["suggested_action"],
  { label: string; variant: "safe" | "warn" | "neutral" | "danger" }
> = {
  buy: { label: "Mock buy", variant: "safe" },
  sell: { label: "Mock sell", variant: "warn" },
  hold: { label: "Hold", variant: "neutral" },
  blocked: { label: "Blocked", variant: "danger" },
};

const TIMELINE_STEPS = ["Policy", "Universe", "Signals", "Weights", "Risk review", "Report"];

function isRiskPreset(value: string | undefined): value is RiskPreset {
  return value === "conservative" || value === "moderate" || value === "aggressive";
}

export function RunPage() {
  const health = useHealth();
  const workingPolicy = useWorkingPolicy();
  const intentRun = useIntentRun();
  const [direction, setDirection] = useState(workingPolicy?.direction ?? "AAA moderate risk");
  const [riskPreset, setRiskPreset] = useState<RiskPreset>(
    isRiskPreset(workingPolicy?.riskProfile) ? workingPolicy.riskProfile : "moderate",
  );

  const draft = useMemo(
    () => buildSafeWorkflowPolicy({ direction, riskPreset }),
    [direction, riskPreset],
  );
  const backendOk = health.isSuccess && health.data.status === "ok";
  const mockMode = health.isSuccess && health.data.default_broker === "mock";

  const handleRun = () => {
    intentRun.mutate(
      {
        text: draft.text,
        user_id: "fixture-user",
        create_order_proposals: true,
      },
      {
        onSuccess: (result) => {
          const metadata = {
            direction,
            symbols: draft.symbols,
            sectors: draft.sectors,
            themes: draft.themes,
            generatedPolicyText: draft.text,
          };
          setWorkingPolicy(result.policy, false, metadata);
          setRunSnapshot({
            policyId: result.policy.policy_id,
            policyVersion: result.policy.version,
            direction,
            riskPreset: draft.riskPreset,
            symbols: draft.symbols,
            sectors: draft.sectors,
            themes: draft.themes,
            generatedPolicyText: draft.text,
            source: "intent-run",
            intent: result,
          });
        },
      },
    );
  };

  return (
    <>
      <PageHeader
        eyebrow="Level 1-2"
        title="Intent Run"
        description="Enter a symbol, sector, or investment direction plus risk appetite. QuantPilot keeps the run mock, limit-only, and live-trading disabled."
        actions={
          <Button size="lg" onClick={handleRun} disabled={intentRun.isPending || !backendOk}>
            <PlayCircle /> {intentRun.isPending ? "Running" : "Run mock workflow"}
          </Button>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Investment intent</CardTitle>
          <CardDescription>One plain-language instruction becomes the policy used for candidate analysis, signals, target weights, rebalance review, and operation reporting.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-[1fr_220px]">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="intent-direction">Symbol, sector, or direction</Label>
            <Textarea
              id="intent-direction"
              rows={4}
              value={direction}
              onChange={(event) => setDirection(event.target.value)}
              placeholder="Examples: AAA moderate risk, technology sector conservative risk, US AI semiconductor aggressive risk"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="intent-risk">Risk preset</Label>
            <Select value={riskPreset} onValueChange={(value) => setRiskPreset(value as RiskPreset)}>
              <SelectTrigger id="intent-risk">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RISK_PRESETS.map((preset) => (
                  <SelectItem key={preset.value} value={preset.value}>
                    {preset.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[12px] leading-relaxed text-muted">
              Cash {RISK_PRESETS.find((preset) => preset.value === riskPreset)?.cashWeight}% floor,
              limit orders only.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 lg:col-span-2">
            {draft.symbols.map((symbol) => (
              <Badge key={symbol} variant="accent">{symbol}</Badge>
            ))}
            {draft.sectors.map((sector) => (
              <Badge key={sector} variant="neutral">{sector}</Badge>
            ))}
            {draft.themes.map((theme) => (
              <Badge key={theme} variant="neutral">{theme}</Badge>
            ))}
            {draft.symbols.length + draft.sectors.length + draft.themes.length === 0 && (
              <Badge variant="neutral">broad fixture universe</Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pre-flight</CardTitle>
          <CardDescription>These checks must stay safe before any mock workflow runs.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
            <PreflightItem ok={backendOk} pending={health.isPending} label="Backend connected" />
            <PreflightItem ok={mockMode} pending={health.isPending} label="Mock broker default" />
            <PreflightItem ok={true} label="Live trading disabled" />
            <PreflightItem ok={true} label="Market orders disabled" />
          </ul>
        </CardContent>
      </Card>

      {intentRun.isError && (
        <ErrorState
          error={intentRun.error}
          context="Intent workflow failed"
          onRetry={handleRun}
        />
      )}

      {intentRun.isPending && <RunTimeline activeIndex={2} failed={false} />}
      {intentRun.isSuccess && <IntentRunResult result={intentRun.data} />}
    </>
  );
}

function PreflightItem({
  ok,
  pending = false,
  label,
}: {
  ok: boolean;
  pending?: boolean;
  label: string;
}) {
  return (
    <li className="flex items-center gap-2.5 rounded-xl border border-hairline bg-surface-raised px-3.5 py-3 text-[13px] shadow-sm">
      {pending ? (
        <CircleDashed className="size-4 shrink-0 animate-spin text-muted" />
      ) : ok ? (
        <CheckCircle2 className="size-4 shrink-0 text-safe" />
      ) : (
        <XCircle className="size-4 shrink-0 text-danger" />
      )}
      <span className={cn(!ok && !pending && "text-danger")}>{label}</span>
    </li>
  );
}

function RunTimeline({ activeIndex, failed }: { activeIndex: number; failed: boolean }) {
  return (
    <ol aria-label="Run progress" className="flex flex-wrap items-center gap-2">
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

function IntentRunResult({ result }: { result: IntentRunResponse }) {
  const universe = result.candidate_analysis.universe;
  const investable = universe.filter((item) => item.block_reason == null);
  const missing = result.focus.missing_preferred_symbols;
  return (
    <div className="flex flex-col gap-5">
      <RunTimeline activeIndex={TIMELINE_STEPS.length - 1} failed={false} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <RunStat label="Candidates" value={universe.length} />
        <RunStat label="Investable" value={investable.length} />
        <RunStat label="Signals" value={result.signals.length} />
        <RunStat label="Rebalance" value={result.rebalance.suggestions.length} />
        <RunStat label="Risk-checked" value={result.safety.risk_checked_order_plan_ids.length} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="safe"><ShieldCheck /> live_trading_enabled: false</Badge>
        <Badge variant="safe">order_submission_enabled: false</Badge>
        <Badge variant="neutral">broker: {result.safety.broker}</Badge>
        <Badge variant="neutral">data: {result.safety.data_mode}</Badge>
        {missing.length > 0 && (
          <Badge variant="warn"><AlertTriangle /> missing data: {missing.join(", ")}</Badge>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Focus diagnostics</CardTitle>
          <CardDescription>The backend uses union matching across supplied symbols, sectors, and themes.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {result.focus.preferred_symbols.map((symbol) => (
            <Badge key={symbol} variant="accent">symbol {symbol}</Badge>
          ))}
          {result.focus.preferred_sectors.map((sector) => (
            <Badge key={sector} variant="neutral">sector {sector}</Badge>
          ))}
          {result.focus.preferred_themes.map((theme) => (
            <Badge key={theme} variant="neutral">theme {theme}</Badge>
          ))}
          {result.focus.preferred_symbols.length + result.focus.preferred_sectors.length + result.focus.preferred_themes.length === 0 && (
            <Badge variant="neutral">broad fixture universe</Badge>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Candidate universe</CardTitle>
          <CardDescription>Rows blocked by focus, liquidity, data readiness, fixture halt, or blocklist do not become order proposals.</CardDescription>
        </CardHeader>
        <CardContent>
          <CandidateTable items={universe} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Rebalance review</CardTitle>
          <CardDescription>Suggestions are mock guidance. Broker submission remains disabled.</CardDescription>
        </CardHeader>
        <CardContent>
          {result.rebalance.suggestions.length === 0 ? (
            <p className="text-[13px] text-muted">No rebalance suggestion was generated for the current focus.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-[13px]">
                <thead>
                  <tr className="border-b border-hairline text-[12px] text-muted">
                    <th className="py-2.5 pr-4 font-medium">Symbol</th>
                    <th className="py-2.5 pr-4 font-medium">Current</th>
                    <th className="py-2.5 pr-4 font-medium">Target</th>
                    <th className="py-2.5 pr-4 font-medium">Action</th>
                    <th className="py-2.5 font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rebalance.suggestions.map((item) => {
                    const meta = SUGGESTED_ACTION_META[item.suggested_action];
                    return (
                      <tr key={item.ticker} className="h-12 border-b border-hairline/60">
                        <td className="pr-4 font-mono font-semibold">{item.ticker}</td>
                        <td className="pr-4 tabular-nums">{formatPercent(item.current_weight)}</td>
                        <td className="pr-4 tabular-nums">{formatPercent(item.target_weight_suggestion)}</td>
                        <td className="pr-4"><Badge variant={meta.variant}>{meta.label}</Badge></td>
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

      <JsonViewer data={result.operation_report} title="Operation report" />
      <JsonViewer data={result} title="Raw intent result" />
    </div>
  );
}

function CandidateTable({ items }: { items: CandidateUniverseItem[] }) {
  if (items.length === 0) {
    return <p className="text-[13px] text-muted">No candidates returned.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-hairline text-[12px] text-muted">
            <th className="py-2.5 pr-4 font-medium">Symbol</th>
            <th className="py-2.5 pr-4 font-medium">Sector</th>
            <th className="py-2.5 pr-4 font-medium">Focus</th>
            <th className="py-2.5 pr-4 font-medium">Readiness</th>
            <th className="py-2.5 font-medium">Block reason</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.ticker} className="h-12 border-b border-hairline/60">
              <td className="pr-4">
                <div className="flex flex-col">
                  <span className="font-mono font-semibold">{item.ticker}</span>
                  <span className="text-[12px] text-muted">{item.name}</span>
                </div>
              </td>
              <td className="pr-4">{item.sector}</td>
              <td className="pr-4">
                <Badge variant={item.focus_match ? "safe" : "neutral"}>
                  {item.focus_match ? "matched" : "not matched"}
                </Badge>
              </td>
              <td className="pr-4">
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant={item.liquidity_pass ? "safe" : "warn"}>liquidity</Badge>
                  <Badge variant={item.data_ready ? "safe" : "warn"}>data</Badge>
                </div>
              </td>
              <td className="text-[12.5px] text-muted">{item.block_reason ?? "investable"}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
