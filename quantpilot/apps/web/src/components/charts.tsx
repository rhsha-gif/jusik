import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useCountUp } from "@/components/ui/stat";

/* ---------------------------------------------------------------------------
 * Token-driven charts.
 *
 * Every color is passed as a `var(--qp-*)` string. SVG resolves CSS custom
 * properties at paint time, so these charts inherit the same palette as the
 * rest of the UI and re-theme instantly on light/dark toggle — no JS color
 * plumbing, no duplicated palette.
 * ------------------------------------------------------------------------- */

const AXIS = "var(--qp-chart-axis)";
const GRID = "var(--qp-chart-grid)";
const AXIS_TICK = { fill: AXIS, fontSize: 11 } as const;

interface TooltipItem {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

interface InjectedTooltipProps {
  active?: boolean;
  payload?: TooltipItem[];
  label?: string | number;
}

function TooltipShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-hairline bg-surface-solid px-3 py-2 text-[12px] shadow-card">
      {children}
    </div>
  );
}

function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/* ── Distribution donut ─────────────────────────────────────────────────── */

export interface DonutDatum {
  name: string;
  value: number;
  color: string;
}

function DonutTooltip({ active, payload }: InjectedTooltipProps) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <TooltipShell>
      <span className="inline-flex items-center gap-2">
        <span
          aria-hidden
          className="size-2.5 rounded-full"
          style={{ backgroundColor: item.color }}
        />
        <span className="font-medium text-ink">{item.name}</span>
        <span className="tabular-nums text-muted">{item.value}</span>
      </span>
    </TooltipShell>
  );
}

export function DistributionDonut({
  data,
  centerLabel,
  height = 188,
}: {
  data: DonutDatum[];
  centerLabel?: string;
  height?: number;
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const animatedTotal = useCountUp(total);
  // Text equivalent of the color-coded slices for screen readers.
  const ariaLabel = `분포: ${data.map((d) => `${d.name} ${d.value}`).join(", ")}`;
  return (
    <div className="relative" style={{ height }} role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="64%"
            outerRadius="94%"
            paddingAngle={data.length > 1 ? 2 : 0}
            cornerRadius={5}
            startAngle={90}
            endAngle={-270}
            stroke="transparent"
            isAnimationActive
          >
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip content={<DonutTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"
      >
        <span className="text-[26px] font-semibold leading-none tabular-nums tracking-tight">
          {Math.round(animatedTotal)}
        </span>
        {centerLabel && (
          <span className="mt-1 text-[10.5px] font-medium uppercase tracking-[0.1em] text-muted">
            {centerLabel}
          </span>
        )}
      </div>
    </div>
  );
}

/** Compact, controllable legend that pairs with any categorical chart. */
export function ChartLegend({ items }: { items: { name: string; color: string; value?: React.ReactNode }[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((item) => (
        <li key={item.name} className="flex items-center gap-2 text-[12.5px]">
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: item.color }}
          />
          <span className="text-muted">{item.name}</span>
          {item.value != null && (
            <span className="ml-auto font-medium tabular-nums text-ink">{item.value}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/* ── Strength histogram ─────────────────────────────────────────────────── */

const STRENGTH_BUCKETS = [
  { label: "0–20", min: 0, max: 0.2 },
  { label: "20–40", min: 0.2, max: 0.4 },
  { label: "40–60", min: 0.4, max: 0.6 },
  { label: "60–80", min: 0.6, max: 0.8 },
  // Upper bound is 1.01 (not 1.0) on purpose: the filter is half-open [min,max),
  // so this keeps strength === 1.0 in the top bucket. Do not "simplify" to 1.0.
  { label: "80–100", min: 0.8, max: 1.01 },
];

function HistogramTooltip({ active, payload, label }: InjectedTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell>
      <p className="font-medium text-ink">강도 {label}%</p>
      <p className="tabular-nums text-muted">{payload[0].value}개 신호</p>
    </TooltipShell>
  );
}

export function StrengthHistogram({ values, height = 188 }: { values: number[]; height?: number }) {
  const data = STRENGTH_BUCKETS.map((bucket) => ({
    label: bucket.label,
    count: values.filter((v) => v >= bucket.min && v < bucket.max).length,
  }));
  const ariaLabel = `강도 구간별 신호 수: ${data.map((d) => `${d.label}% ${d.count}개`).join(", ")}`;
  return (
    <div style={{ height }} role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -4 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={AXIS_TICK} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={AXIS_TICK} width={28} />
          <Tooltip cursor={{ fill: "var(--qp-accent-soft)" }} content={<HistogramTooltip />} />
          <Bar dataKey="count" radius={[5, 5, 0, 0]} fill="var(--qp-accent)" isAnimationActive />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ── Rebalance weights: current vs target ───────────────────────────────── */

export interface WeightDatum {
  label: string;
  current: number;
  target: number;
}

function WeightTooltip({ active, payload, label }: InjectedTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <TooltipShell>
      <p className="font-mono font-medium text-ink">{label}</p>
      <div className="mt-1 flex flex-col gap-0.5">
        {payload.map((item) => (
          <span key={String(item.dataKey)} className="inline-flex items-center gap-2">
            <span
              aria-hidden
              className="size-2.5 rounded-full"
              style={{ backgroundColor: item.color }}
            />
            <span className="text-muted">{item.name}</span>
            <span className="ml-auto tabular-nums text-ink">
              {typeof item.value === "number" ? pct(item.value) : item.value}
            </span>
          </span>
        ))}
      </div>
    </TooltipShell>
  );
}

export function WeightsBars({ data, height = 248 }: { data: WeightDatum[]; height?: number }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 4 }} barGap={3} barCategoryGap="26%">
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tick={{ fill: AXIS, fontSize: 11, fontFamily: "var(--font-mono)" }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={46}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={AXIS_TICK}
          />
          <Tooltip cursor={{ fill: "var(--qp-accent-soft)" }} content={<WeightTooltip />} />
          <Bar dataKey="current" name="현재" fill="var(--qp-faint)" radius={[4, 4, 0, 0]} isAnimationActive />
          <Bar dataKey="target" name="목표" fill="var(--qp-accent)" radius={[4, 4, 0, 0]} isAnimationActive />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
