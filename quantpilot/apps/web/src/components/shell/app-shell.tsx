import { NavLink, Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  FlaskConical,
  Gauge,
  ListChecks,
  Radar,
  ScrollText,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBase } from "@/lib/api";
import { useHealth } from "@/lib/queries";
import { HealthPill } from "./health-pill";
import { SafetyBanner } from "./safety-banner";
import { ThemeToggle } from "./theme-toggle";

const NAV_ITEMS = [
  { to: "/", label: "오버뷰", icon: Gauge, end: true },
  { to: "/research", label: "리서치", icon: FlaskConical },
  { to: "/policies", label: "정책 스튜디오", icon: ListChecks },
  { to: "/signals", label: "신호 보드", icon: Radar },
  { to: "/run", label: "Level 1-2 실행", icon: Activity },
  { to: "/jobs", label: "작업 & 로그", icon: ScrollText },
  { to: "/settings", label: "설정 & 안전", icon: Settings },
];

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative flex size-9 items-center justify-center rounded-[13px] bg-gradient-to-br from-accent to-accent-2 text-white shadow-[0_4px_14px_-2px_var(--qp-accent-soft),0_1px_0_rgba(255,255,255,0.25)_inset]">
        <ShieldCheck className="size-5" strokeWidth={2.2} />
      </span>
      <div className="leading-tight">
        <p className="text-[15px] font-semibold tracking-tight">QuantPilot</p>
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          Pre-Harness
        </p>
      </div>
    </div>
  );
}

export function AppShell() {
  const location = useLocation();
  const reducedMotion = useReducedMotion();
  const health = useHealth();
  const online = health.data?.status === "ok";

  return (
    <div className="flex h-full">
      <aside
        aria-label="주 메뉴"
        className="hidden w-[256px] shrink-0 flex-col border-r border-hairline bg-surface/80 backdrop-blur-xl lg:flex"
      >
        <div className="flex h-16 items-center px-5">
          <Wordmark />
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-3 py-3">
          <p className="px-3.5 pb-2 pt-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">
            워크스페이스
          </p>
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[13.5px] font-medium transition-colors",
                  isActive ? "text-accent" : "text-muted hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="nav-active"
                      aria-hidden
                      className="absolute inset-0 rounded-xl border border-accent/15 bg-accent-soft"
                      transition={
                        reducedMotion
                          ? { duration: 0 }
                          : { type: "spring", stiffness: 480, damping: 38 }
                      }
                    />
                  )}
                  <Icon
                    className={cn(
                      "relative z-10 size-4 transition-colors",
                      !isActive && "text-faint group-hover:text-muted",
                    )}
                  />
                  <span className="relative z-10">{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 pb-4">
          <div className="rounded-xl border border-hairline bg-surface-solid/60 px-3.5 py-3">
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className={cn(
                  "size-1.5 rounded-full",
                  online ? "bg-safe" : "animate-pulse bg-warn",
                )}
              />
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-faint">
                API Base
              </p>
            </div>
            <p className="mt-1 truncate font-mono text-[11.5px] text-muted">
              {getApiBase()}
            </p>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-hairline bg-surface/70 px-5 backdrop-blur-xl lg:px-6">
          <div className="flex items-center gap-3 lg:hidden">
            <span className="flex size-8 items-center justify-center rounded-[11px] bg-gradient-to-br from-accent to-accent-2 text-white shadow-sm">
              <ShieldCheck className="size-4" strokeWidth={2.2} />
            </span>
            <span className="text-[15px] font-semibold tracking-tight">QuantPilot</span>
          </div>
          <nav aria-label="모바일 메뉴" className="flex gap-1 overflow-x-auto lg:hidden">
            {NAV_ITEMS.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "whitespace-nowrap rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors",
                    isActive ? "bg-accent-soft text-accent" : "text-muted",
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-3">
            <HealthPill />
            <ThemeToggle />
          </div>
        </header>

        <SafetyBanner dataMode={health.data?.data_mode ?? "fixture"} />

        <main className="min-w-0 flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={reducedMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reducedMotion ? undefined : { opacity: 0, y: -4 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className="mx-auto flex w-full max-w-6xl flex-col gap-7 px-6 py-8 lg:px-10"
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
