import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  FlaskConical,
  ListChecks,
  Lock,
  PlayCircle,
  Radar,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { JsonViewer } from "@/components/json-viewer";
import { OfflineState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useHealth, useRunSmoke } from "@/lib/queries";
import { formatDuration } from "@/lib/utils";

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
    title: "Level 1-2 실행",
    description: "모의 파이프라인 전체를 실행하고 결과 리뷰",
  },
];

export function OverviewPage() {
  const health = useHealth();
  const smoke = useRunSmoke();

  return (
    <>
      <PageHeader
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
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={health.data?.status === "ok" ? "safe" : "warn"}>
                      status: {health.data?.status}
                    </Badge>
                    <Badge variant={health.data?.live_trading_enabled ? "danger" : "safe"}>
                      live_trading_enabled: {String(health.data?.live_trading_enabled)}
                    </Badge>
                    <Badge variant="neutral">broker: {health.data?.default_broker}</Badge>
                  </div>
                  <JsonViewer data={health.data} title="Raw JSON (health)" />
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
                <SmokeStat label="신호" value={smoke.data.signals} />
                <SmokeStat label="주문 플랜" value={smoke.data.orders.length} />
                <SmokeStat label="모의 체결" value={smoke.data.fills} />
                <SmokeStat label="감사 이벤트" value={smoke.data.audit_events} />
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
            className="group flex flex-col gap-3 rounded-card border border-hairline bg-surface p-5 shadow-card backdrop-blur-xl transition-transform hover:-translate-y-0.5"
          >
            <span className="flex size-9 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <Icon className="size-4.5" />
            </span>
            <div>
              <h3 className="flex items-center gap-1.5 text-[15px] font-semibold">
                {title}
                <ArrowRight className="size-3.5 text-muted transition-transform group-hover:translate-x-0.5" />
              </h3>
              <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{description}</p>
            </div>
          </Link>
        ))}
      </section>
    </>
  );
}

function SmokeStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-hairline bg-surface-solid px-4 py-3">
      <p className="text-[12px] text-muted">{label}</p>
      <p className="mt-0.5 text-[22px] font-semibold tabular-nums">{value}</p>
    </div>
  );
}
