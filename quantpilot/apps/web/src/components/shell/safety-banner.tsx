import { ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const DATA_MODE_LABELS: Record<string, string> = {
  fixture: "Fixture 데이터",
  local_historical: "로컬 히스토리",
  external_historical: "외부 히스토리",
  realtime_market_data: "실시간 데이터",
  paper_trading: "페이퍼 트레이딩",
  live_trading: "실거래 차단됨",
};

const DATA_MODE_VARIANT: Record<string, "neutral" | "warn" | "danger"> = {
  fixture: "neutral",
  local_historical: "neutral",
  external_historical: "warn",
  realtime_market_data: "warn",
  paper_trading: "warn",
  live_trading: "danger",
};

interface SafetyBannerProps {
  dataMode?: string;
}

/**
 * Always-visible safety strip (design.md §9). The pre-harness never places
 * live broker orders; these labels must not be hidden.
 */
export function SafetyBanner({ dataMode = "fixture" }: SafetyBannerProps) {
  const modeLabel = DATA_MODE_LABELS[dataMode] ?? dataMode;
  const modeVariant = DATA_MODE_VARIANT[dataMode] ?? "warn";

  return (
    <div
      role="note"
      aria-label="안전 모드 안내"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-hairline bg-safe-soft/60 px-6 py-2"
    >
      <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-safe">
        <ShieldCheck className="size-3.5" />
        모의 브로커 활성 (Mock Broker)
      </span>
      <span className="hidden text-[12px] text-muted sm:inline">
        이 인터페이스는 로컬 QuantPilot 프리하니스만 제어하며, 실제 증권사 주문을 내지 않습니다.
      </span>
      <span className="ml-auto flex items-center gap-1.5">
        <Badge variant="safe">실거래 비활성</Badge>
        <Badge variant="neutral">지정가 주문만</Badge>
        <Badge variant={modeVariant}>{modeLabel}</Badge>
      </span>
    </div>
  );
}
