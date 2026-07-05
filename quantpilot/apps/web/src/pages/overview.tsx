import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  BellRing,
  Bot,
  Database,
  FileCheck2,
  FlaskConical,
  Layers,
  ListChecks,
  Lock,
  PlayCircle,
  Radar,
  Receipt,
  RefreshCw,
  Server,
  ShieldCheck,
  Signal,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Stat } from "@/components/ui/stat";
import { Skeleton } from "@/components/ui/misc";
import { JsonViewer } from "@/components/json-viewer";
import { OfflineState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useHealth, useRunSmoke } from "@/lib/queries";
import type { HealthResponse } from "@/lib/types";
import { cn, formatDuration } from "@/lib/utils";

const DATA_MODE_LABELS: Record<string, string> = {
  fixture: "Fixture",
  local_historical: "로컬 히스토리",
  external_historical: "외부 히스토리",
  realtime_market_data: "실시간",
  paper_trading: "페이퍼",
  live_trading: "실거래 차단",
};

const SAFETY_DEFAULTS = [
  { key: "LIVE_TRADING_ENABLED", value: "false", label: "실거래 비활성" },
  { key: "BROKER_MODE", value: "mock", label: "모의 브로커" },
  { key: "DEFAULT_ORDER_TYPE", value: "limit", label: "기본 지정가 주문" },
  { key: "MARKET_ORDERS_ENABLED", value: "false", label: "시장가 주문 차단" },
];

const WORKFLOWS = [
  {
    to: "/research",
    icon: FlaskConical,
    title: "리서치",
    description: "후보 유니버스 생성과 애널리스트 리포트 요청",
  },
  {
    to: "/policies",
    icon: ListChecks,
    title: "정책 스튜디오",
    description: "정책 텍스트를 파싱·미리보기·확정",
  },
  {
    to: "/signals",
    icon: Radar,
    title: "신호 보드",
    description: "신호의 방향·강도·사유를 운영자 보드로 확인",
  },
  {
    to: "/run",
    icon: Activity,
    title: "Level 1-2 자동매매",
    description: "프로그램이 타이밍을 판단해 모의 계좌에서 자동 체결",
  },
  {
    to: "/execution",
    icon: BellRing,
    title: "승인 알림",
    description: "실거래 타이밍 포착 시 알림 → 승인 → 시스템 집행",
  },
  {
    to: "/operator",
    icon: Bot,
    title: "Level 5 운영자",
    description: "운영자 안전 상태 확인과 dry-run 1회 실행 (모의/페이퍼 한정)",
  },
];

export function OverviewPage() {
  const health = useHealth();
  const smoke = useRunSmoke();

  return (
    <>
      <PageHeader
        eyebrow="Operator Console"
        title="QuantPilot Operator Pre-Harness"
        description="안전한 모의 환경입니다. 실제 증권사 주문은 비활성화되어 있으며, 모든 실행은 로컬 fixture 데이터를 사용합니다."
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void health.refetch()}
            disabled={health.isFetching}
          >
            <RefreshCw className={health.isFetching ? "animate-spin" : ""} />
            새로고침
          </Button>
        }
      />

      {health.isError ? (
        <OfflineState onRetry={() => void health.refetch()} />
      ) : (
        <>
        {health.isSuccess && <OverviewKpis health={health.data} />}
        <TradingModes />
        <div className="grid gap-5 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="size-4.5 text-safe" /> 백엔드 상태
              </CardTitle>
              <CardDescription>GET /api/health</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {health.isPending ? (
                <Skeleton className="h-20" />
              ) : (
                <>
                  <p className="text-[13px] leading-relaxed text-muted">
                    핵심 상태는 위 요약 줄에 표시됩니다. 아래에서 원본 응답 전체를 확인할 수 있습니다.
                  </p>
                  <JsonViewer data={health.data} title="Raw JSON (health)" defaultOpen />
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Lock className="size-4.5 text-muted" /> 안전 기본값
              </CardTitle>
              <CardDescription>프리하니스가 강제하는 변경 불가 기본값</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="flex flex-col gap-2.5">
                {SAFETY_DEFAULTS.map((item) => (
                  <li
                    key={item.key}
                    className="flex items-center justify-between gap-3 rounded-xl border border-hairline bg-surface-solid px-3.5 py-2.5"
                  >
                    <span className="text-[13px] font-medium">{item.label}</span>
                    <code className="font-mono text-[12px] text-muted">
                      {item.key}={item.value}
                    </code>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
        </>
      )}

      <Card>
        <CardHeader className="flex-row items-start justify-between">
          <div className="flex flex-col gap-1">
            <CardTitle className="flex items-center gap-2">
              <PlayCircle className="size-4.5 text-accent" /> 스모크 테스트
            </CardTitle>
            <CardDescription>
              POST /api/harness/run-smoke — 정책 파싱부터 모의 체결·리포트까지 전체 경로를
              검증합니다. 모의 브로커만 사용합니다.
            </CardDescription>
          </div>
          <Button onClick={() => smoke.mutate()} disabled={smoke.isPending || health.isError}>
            {smoke.isPending ? "실행 중…" : "스모크 테스트 실행"}
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {smoke.isError && (
            <p role="alert" className="text-[13px] text-danger">
              스모크 테스트 실패: {smoke.error instanceof Error ? smoke.error.message : "알 수 없는 오류"}
            </p>
          )}
          {smoke.isSuccess && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="신호" value={smoke.data.signals} icon={Signal} tone="accent" />
                <Stat label="주문 플랜" value={smoke.data.orders.length} icon={Layers} />
                <Stat label="모의 체결" value={smoke.data.fills} icon={FileCheck2} />
                <Stat label="감사 이벤트" value={smoke.data.audit_events} icon={Receipt} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="safe">live_trading_enabled: false</Badge>
                <Badge variant="neutral">broker: {smoke.data.broker}</Badge>
                <Badge variant="neutral">{smoke.data.execution_mode}</Badge>
                {typeof smoke.submittedAt === "number" && (
                  <span className="text-[12px] text-muted">
                    소요 {formatDuration(Date.now() - smoke.submittedAt)} 이내 완료
                  </span>
                )}
              </div>
              <JsonViewer data={smoke.data} title="Raw JSON (smoke result)" />
            </>
          )}
          {smoke.isIdle && !smoke.isSuccess && (
            <p className="text-[13px] text-muted">아직 실행 기록이 없습니다.</p>
          )}
        </CardContent>
      </Card>

      <section aria-label="워크플로 바로가기" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {WORKFLOWS.map(({ to, icon: Icon, title, description }) => (
          <Link
            key={to}
            to={to}
            className="panel group flex flex-col gap-3 p-5 transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-1 hover:border-hairline-strong hover:shadow-lg"
          >
            <span className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-accent-2 text-white shadow-[0_4px_12px_-3px_var(--qp-accent-soft)] transition-transform duration-200 group-hover:scale-105">
              <Icon className="size-4.5" />
            </span>
            <div>
              <h3 className="flex items-center justify-between gap-1.5 text-[15px] font-semibold">
                {title}
                <ArrowRight className="size-4 text-faint transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-accent" />
              </h3>
              <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{description}</p>
            </div>
          </Link>
        ))}
      </section>
    </>
  );
}

/**
 * The two ways QuantPilot actually executes trades, side by side. This is the
 * answer to "does it only suggest, or does it trade?" — Level 1-2 trades itself
 * on the mock account; real trading routes through an approval alert and then the
 * system (not the user) submits.
 */
function TradingModes() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4.5 text-accent" /> 매매 동작 방식
        </CardTitle>
        <CardDescription>단계에 따라 두 가지 방식으로 매매가 집행됩니다.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <ModeTile
          icon={Bot}
          tone="accent"
          badge="모의 · Level 1-2"
          title="프로그램 자동매매"
          body="실제 주식이 아니므로 프로그램이 매매 타이밍을 스스로 판단하고 모의 계좌에 자동으로 체결합니다. 사람의 승인이 필요 없습니다."
          to="/run"
          cta="자동매매 실행"
        />
        <ModeTile
          icon={BellRing}
          tone="warn"
          badge="실거래"
          title="알림 → 승인 → 시스템 집행"
          body="실거래 타이밍이 포착되면 사용자에게 알림을 보냅니다. 사용자가 승인하면, 사용자가 직접 매매하지 않고 시스템이 주문을 직접 집행합니다."
          to="/execution"
          cta="승인 알림 열기"
        />
      </CardContent>
    </Card>
  );
}

function ModeTile({
  icon: Icon,
  tone,
  badge,
  title,
  body,
  to,
  cta,
}: {
  icon: typeof Bot;
  tone: "accent" | "warn";
  badge: string;
  title: string;
  body: string;
  to: string;
  cta: string;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-hairline bg-surface-solid/60 p-4">
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className={cn(
            "flex size-9 items-center justify-center rounded-xl",
            tone === "accent" ? "bg-accent-soft text-accent" : "bg-warn-soft text-warn",
          )}
        >
          <Icon className="size-4.5" />
        </span>
        <Badge variant={tone === "accent" ? "accent" : "warn"}>{badge}</Badge>
      </div>
      <div>
        <h3 className="text-[14.5px] font-semibold">{title}</h3>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{body}</p>
      </div>
      <Link
        to={to}
        className="mt-auto inline-flex w-fit items-center gap-1.5 text-[12.5px] font-medium text-accent hover:underline"
      >
        {cta}
        <ArrowRight className="size-3.5" />
      </Link>
    </div>
  );
}

/**
 * Severity for the data-mode tile, kept in lockstep with the always-on
 * SafetyBanner: live_trading is the most dangerous config (red), any other
 * unsafe mode is caution (amber), safe modes are green.
 */
function dataModeTone(mode: string, safe: boolean): "safe" | "warn" | "danger" {
  if (mode === "live_trading") return "danger";
  if (!safe) return "warn";
  return "safe";
}

/** Scannable status ribbon — the at-a-glance command-center line. */
function OverviewKpis({ health }: { health: HealthResponse }) {
  const online = health.status === "ok";
  const dataModeLabel = DATA_MODE_LABELS[health.data_mode] ?? health.data_mode;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Stat
        label="백엔드"
        value={online ? "연결됨" : "점검 필요"}
        icon={Server}
        tone={online ? "safe" : "warn"}
        hint={`status: ${health.status}`}
      />
      <Stat
        label="데이터 모드"
        value={dataModeLabel}
        icon={Database}
        tone={dataModeTone(health.data_mode, health.data_mode_safe)}
        hint={health.data_mode_error ?? (health.data_mode_safe ? "안전 모드" : "주의")}
      />
      <Stat label="브로커" value={health.default_broker} icon={Bot} hint="모의 브로커" />
      <Stat
        label="실거래"
        value={health.live_trading_enabled ? "활성" : "차단"}
        icon={Lock}
        tone={health.live_trading_enabled ? "danger" : "safe"}
        hint="LIVE_TRADING_ENABLED=false"
      />
    </div>
  );
}
