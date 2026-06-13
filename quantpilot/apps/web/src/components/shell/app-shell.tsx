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

export function AppShell() {
  const location = useLocation();
  const reducedMotion = useReducedMotion();
  const health = useHealth();

  return (
    <div className="flex h-full">
      <aside
        aria-label="주 메뉴"
        className="hidden w-[248px] shrink-0 flex-col border-r border-hairline bg-surface backdrop-blur-xl lg:flex"
      >
        <div className="flex h-16 items-center gap-2.5 px-6">
          <span className="flex size-8 items-center justify-center rounded-xl bg-ink text-bg">
            <ShieldCheck className="size-4.5" />
          </span>
          <div className="leading-tight">
            <p className="text-[15px] font-semibold tracking-tight">QuantPilot</p>
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted">
              Pre-Harness
            </p>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[13.5px] font-medium text-muted transition-colors hover:bg-accent-soft hover:text-ink",
                  isActive && "bg-accent-soft text-accent",
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-hairline px-6 py-4">
          <p className="text-[11px] font-medium text-muted">API Base</p>
          <p className="mt-0.5 truncate font-mono text-[12px] text-ink">{getApiBase()}</p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-hairline bg-surface px-6 backdrop-blur-xl">
          <div className="flex items-center gap-3 lg:hidden">
            <span className="flex size-7 items-center justify-center rounded-lg bg-ink text-bg">
              <ShieldCheck className="size-4" />
            </span>
            <span className="text-[15px] font-semibold">QuantPilot</span>
          </div>
          <nav aria-label="모바일 메뉴" className="flex gap-1 overflow-x-auto lg:hidden">
            {NAV_ITEMS.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "whitespace-nowrap rounded-full px-3 py-1.5 text-[12px] font-medium text-muted",
                    isActive && "bg-accent-soft text-accent",
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
              transition={{ duration: 0.18, ease: "easeOut" }}
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
