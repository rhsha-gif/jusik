import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTone = "default" | "accent" | "safe" | "warn" | "danger";

const TONE_ICON: Record<StatTone, string> = {
  default: "bg-surface-solid text-muted",
  accent: "bg-accent-soft text-accent",
  safe: "bg-safe-soft text-safe",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
};

// accent/warn value colors clear AA only at the large (26px) value size below.
// If a Stat value is ever rendered small, re-check contrast for these tones.
const TONE_VALUE: Record<StatTone, string> = {
  default: "text-ink",
  accent: "text-accent",
  safe: "text-safe",
  warn: "text-warn",
  danger: "text-danger",
};

/**
 * Animates a number from 0 → target with an ease-out curve. Returns the
 * target verbatim when the user prefers reduced motion or the value is not a
 * finite number, so server/test renders and accessibility settings stay exact.
 */
export function useCountUp(target: number, durationMs = 650): number {
  const reduced = usePrefersReducedMotion();
  const [value, setValue] = React.useState(() => (reduced ? target : 0));

  React.useEffect(() => {
    if (reduced || !Number.isFinite(target)) {
      setValue(target);
      return;
    }
    if (typeof requestAnimationFrame !== "function") {
      setValue(target);
      return;
    }
    let raf = 0;
    let start: number | null = null;
    const from = 0;
    const tick = (now: number) => {
      if (start === null) start = now;
      const t = Math.min(1, (now - start) / durationMs);
      // easeOutCubic — fast settle, gentle landing.
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs, reduced]);

  return value;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(media.matches);
    const onChange = () => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

function AnimatedNumber({ value, decimals }: { value: number; decimals: number }) {
  const animated = useCountUp(value);
  const rounded =
    decimals > 0 ? animated.toFixed(decimals) : Math.round(animated).toString();
  return <>{rounded}</>;
}

export interface StatProps {
  label: string;
  /** Numeric values count up; strings render verbatim. */
  value: number | string;
  icon?: LucideIcon;
  tone?: StatTone;
  /** Decimal places used while counting a numeric value. */
  decimals?: number;
  /** Optional suffix appended after the value (e.g. "%", "건"). */
  suffix?: string;
  hint?: React.ReactNode;
  className?: string;
}

/**
 * Unified metric tile used across Overview / Run / Signals. Replaces the
 * three near-identical inline stat tiles with one accessible, animated card.
 */
export function Stat({
  label,
  value,
  icon: Icon,
  tone = "default",
  decimals = 0,
  suffix,
  hint,
  className,
}: StatProps) {
  return (
    <div
      className={cn(
        "group relative flex flex-col gap-1 overflow-hidden rounded-xl border border-hairline bg-surface-raised px-4 py-3.5 shadow-sm transition-colors duration-200 hover:border-hairline-strong",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
          {label}
        </p>
        {Icon && (
          <span
            aria-hidden
            className={cn(
              "flex size-6 items-center justify-center rounded-lg [&_svg]:size-3.5",
              TONE_ICON[tone],
            )}
          >
            <Icon />
          </span>
        )}
      </div>
      <p
        className={cn(
          "text-[26px] font-semibold leading-none tabular-nums tracking-tight",
          TONE_VALUE[tone],
        )}
      >
        {typeof value === "number" ? (
          <AnimatedNumber value={value} decimals={decimals} />
        ) : (
          value
        )}
        {suffix && <span className="ml-0.5 text-[15px] font-medium text-muted">{suffix}</span>}
      </p>
      {hint && <p className="text-[11.5px] leading-snug text-muted">{hint}</p>}
    </div>
  );
}
