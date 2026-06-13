import { cn } from "@/lib/utils";
import { useHealth } from "@/lib/queries";

type HealthLevel = "healthy" | "degraded" | "offline" | "loading";

export function useHealthLevel(): HealthLevel {
  const health = useHealth();
  if (health.isPending) return "loading";
  if (health.isError) return "offline";
  return health.data?.status === "ok" ? "healthy" : "degraded";
}

const LABELS: Record<HealthLevel, string> = {
  healthy: "백엔드 연결됨",
  degraded: "백엔드 상태 확인 필요",
  offline: "백엔드 오프라인",
  loading: "연결 확인 중…",
};

export function HealthPill({ className }: { className?: string }) {
  const level = useHealthLevel();
  return (
    <span
      role="status"
      className={cn(
        "inline-flex items-center gap-2 whitespace-nowrap rounded-full border px-3 py-1 text-[12px] font-medium",
        level === "healthy" && "border-transparent bg-safe-soft text-safe",
        level === "degraded" && "border-transparent bg-warn-soft text-warn",
        level === "offline" && "border-transparent bg-danger-soft text-danger",
        level === "loading" && "border-hairline bg-surface-solid text-muted",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          level === "healthy" && "bg-safe",
          level === "degraded" && "bg-warn",
          level === "offline" && "bg-danger",
          level === "loading" && "animate-pulse bg-muted",
        )}
      />
      {LABELS[level]}
    </span>
  );
}
