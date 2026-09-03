import { useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { setOperatorToken } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

const DATA_MODE_LABELS: Record<string, string> = {
  fixture: "Fixture 데이터",
  local_historical: "로컬 히스토리",
  external_historical: "외부 히스토리",
  realtime_market_data: "실시간 데이터",
  paper_trading: "페이퍼 트레이딩",
  live_trading: "실거래 차단됨",
  live_trading_candidate: "실거래 후보 차단됨",
  live_canary: "실거래 카나리 차단됨",
  live_scaled: "실거래 확장 차단됨",
};

const DATA_MODE_VARIANT: Record<string, "neutral" | "warn" | "danger"> = {
  fixture: "neutral",
  local_historical: "neutral",
  external_historical: "warn",
  realtime_market_data: "warn",
  paper_trading: "warn",
  live_trading: "danger",
  live_trading_candidate: "danger",
  live_canary: "danger",
  live_scaled: "danger",
};

interface SafetyBannerProps {
  dataMode?: string;
  health?: HealthResponse;
}

/**
 * Always-visible safety strip (design.md §9). The pre-harness never places
 * live broker orders; these labels must not be hidden.
 */
export function SafetyBanner({ dataMode = "fixture", health }: SafetyBannerProps) {
  const [token, setToken] = useState("");
  const resolvedDataMode = health?.data_mode ?? dataMode;
  const blocked = health?.status === "blocked";
  const liveTradingEnabled = health?.live_trading_enabled ?? false;
  const marketOrdersEnabled = health?.market_orders_enabled ?? false;
  const fullyAutomatedOperatorEnabled = health?.fully_automated_operator_enabled ?? false;
  const brokerMode = health?.default_broker ?? "mock";
  const Icon = blocked ? ShieldAlert : ShieldCheck;
  const brokerLabel =
    brokerMode === "mock" ? "모의 브로커 활성 (Mock Broker)" : `${brokerMode} 브로커 구성`;
  const modeLabel = DATA_MODE_LABELS[resolvedDataMode] ?? resolvedDataMode;
  const modeVariant = blocked ? "danger" : (DATA_MODE_VARIANT[resolvedDataMode] ?? "warn");

  return (
    <div
      role="note"
      aria-label="안전 모드 안내"
      className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-hairline bg-gradient-to-r px-5 py-2 lg:px-6 ${
        blocked
          ? "from-danger-soft/80 via-danger-soft/30 to-transparent"
          : "from-safe-soft/80 via-safe-soft/30 to-transparent"
      }`}
    >
      <span
        className={`inline-flex items-center gap-1.5 text-[12.5px] font-semibold ${
          blocked ? "text-danger" : "text-safe"
        }`}
      >
        <span
          className={`flex size-5 items-center justify-center rounded-md ${
            blocked ? "bg-danger-soft text-danger" : "bg-safe-soft text-safe"
          }`}
        >
          <Icon className="size-3.5" />
        </span>
        {brokerLabel}
      </span>
      <span className="hidden text-[12px] text-muted sm:inline">
        이 인터페이스는 로컬 QuantPilot 프리하니스만 제어하며, 실제 증권사 주문을 내지 않습니다.
      </span>
      <label className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
        운영자 토큰
        <input
          aria-label="운영자 토큰 (메모리에만 보관)"
          autoComplete="off"
          className="w-36 rounded-md border border-hairline bg-surface-solid px-2 py-1 font-mono text-[11px] text-ink"
          onChange={(event) => {
            setToken(event.target.value);
            setOperatorToken(event.target.value);
          }}
          placeholder="메모리에만 보관"
          type="password"
          value={token}
        />
      </label>
      <span className="ml-auto flex items-center gap-1.5">
        {health && (
          <Badge variant={blocked ? "danger" : "safe"}>
            {blocked ? "안전 설정 차단" : "안전 설정 정상"}
          </Badge>
        )}
        {fullyAutomatedOperatorEnabled && <Badge variant="danger">완전 자동 운영 활성</Badge>}
        <Badge variant={liveTradingEnabled ? "danger" : "safe"}>
          {liveTradingEnabled ? "실거래 활성" : "실거래 비활성"}
        </Badge>
        <Badge variant={marketOrdersEnabled ? "danger" : "neutral"}>
          {marketOrdersEnabled ? "시장가 주문 활성" : "지정가 주문만"}
        </Badge>
        <Badge variant={modeVariant}>{modeLabel}</Badge>
      </span>
    </div>
  );
}
