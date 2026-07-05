import { useState } from "react";
import { CheckCircle2, FlaskConical, Radar, ShieldCheck, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import {
  useApproveStrategyTicket,
  useCreateStrategyDraft,
  useCreateStrategyTicket,
  useValidateStrategyDraft,
} from "@/lib/queries";
import type { StrategyApprovalTicket, StrategyDraft, StrategyDraftValidation } from "@/lib/types";

const METRIC_LABELS: Record<string, string> = {
  total_return: "총수익률",
  max_drawdown: "최대 낙폭",
  simplified_sharpe: "Sharpe(간이)",
  hit_rate: "승률",
  filled_trades: "체결 수",
  number_of_blocked_trades: "차단 수",
  exposure: "평균 노출도",
  turnover: "회전율",
};

function formatMetric(name: string, value: number): string {
  if (["total_return", "max_drawdown", "hit_rate", "exposure"].includes(name)) {
    return `${(value * 100).toFixed(2)}%`;
  }
  if (["filled_trades", "number_of_blocked_trades"].includes(name)) {
    return String(Math.round(value));
  }
  return value.toFixed(3);
}

export function StudioPage() {
  const createDraft = useCreateStrategyDraft();
  const validateDraft = useValidateStrategyDraft();
  const createTicket = useCreateStrategyTicket();
  const approveTicket = useApproveStrategyTicket();

  const [symbolsInput, setSymbolsInput] = useState("");
  const [sectorsInput, setSectorsInput] = useState("technology");
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [validation, setValidation] = useState<StrategyDraftValidation | null>(null);
  const [ticket, setTicket] = useState<StrategyApprovalTicket | null>(null);

  const splitList = (raw: string) =>
    raw
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  const onCreateDraft = () => {
    setValidation(null);
    setTicket(null);
    createDraft.mutate(
      { symbols: splitList(symbolsInput), sectors: splitList(sectorsInput), note },
      { onSuccess: (data) => setDraft(data) },
    );
  };

  const onValidate = () => {
    if (!draft) return;
    validateDraft.mutate(draft.draft_id, {
      onSuccess: (data) => {
        setValidation(data);
        setDraft(data.draft);
      },
    });
  };

  const onCreateTicket = () => {
    if (!draft || !validation) return;
    createTicket.mutate(
      {
        strategy_id: draft.strategy_id,
        strategy_version: draft.strategy_version,
        spec_hash: draft.spec_hash,
        backtest_report_id: validation.backtest_report_id,
        requested_execution_level: "level_3",
        capital_budget_pct: 0.2,
      },
      { onSuccess: (data) => setTicket(data) },
    );
  };

  const onApproveTicket = () => {
    if (!ticket) return;
    approveTicket.mutate(
      { ticketId: ticket.ticket_id, approvedBy: "user" },
      { onSuccess: (data) => setTicket(data) },
    );
  };

  return (
    <>
      <PageHeader
        eyebrow="Strategy Studio"
        title="전략 수립 스튜디오"
        description="관심 섹터나 종목을 고르면 시스템이 전략 초안을 만들고, 실비용(한투 API 수수료·증권거래세) 기준 백테스트로 검증한 뒤에만 승인 버튼이 열립니다. 승인은 즉시 매수가 아니라 감시 상태 무장(arming)입니다 — 진입·청산 타이밍은 전략이 셋업 완성 시점에 판단합니다."
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4" /> 1. 전략 초안 만들기
          </CardTitle>
          <CardDescription>
            쉼표로 구분해 입력합니다. 종목 코드 또는 섹터 중 하나 이상이 필요하며, 매칭되는
            유니버스가 없으면 초안 생성이 거부됩니다 (fail-closed).
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-[13px]">
            <span className="text-muted">관심 종목 (예: 005930, 000660)</span>
            <input
              value={symbolsInput}
              onChange={(event) => setSymbolsInput(event.target.value)}
              className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-2.5"
              placeholder="쉼표로 구분한 종목 코드"
            />
          </label>
          <label className="flex flex-col gap-1 text-[13px]">
            <span className="text-muted">관심 섹터 (예: technology, battery)</span>
            <input
              value={sectorsInput}
              onChange={(event) => setSectorsInput(event.target.value)}
              className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-2.5"
              placeholder="쉼표로 구분한 섹터"
            />
          </label>
          <label className="flex flex-col gap-1 text-[13px]">
            <span className="text-muted">메모 (선택)</span>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="rounded-xl border border-hairline bg-surface-solid px-3.5 py-2.5"
              placeholder="이 전략을 원하는 이유"
            />
          </label>
          <Button onClick={onCreateDraft} disabled={createDraft.isPending} className="w-fit">
            <Sparkles /> {createDraft.isPending ? "초안 생성 중…" : "전략 초안 생성"}
          </Button>
          {createDraft.isError && (
            <ErrorState error={createDraft.error} context="전략 초안 생성에 실패했습니다" />
          )}
        </CardContent>
      </Card>

      {draft && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Radar className="size-4" /> 2. 초안 검토와 백테스트 검증
            </CardTitle>
            <CardDescription>{draft.rationale}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="neutral">draft: {draft.draft_id}</Badge>
              <Badge variant="neutral">
                {draft.strategy_id} v{draft.strategy_version}
              </Badge>
              <Badge variant={draft.status === "validated" ? "safe" : "warn"}>{draft.status}</Badge>
            </div>
            <p className="text-[13px] text-muted">
              유니버스 {draft.universe_symbols.length}종목: {draft.universe_symbols.join(", ")}
            </p>
            <Button onClick={onValidate} disabled={validateDraft.isPending} className="w-fit">
              <FlaskConical /> {validateDraft.isPending ? "백테스트 실행 중…" : "백테스트로 검증"}
            </Button>
            {validateDraft.isError && (
              <ErrorState error={validateDraft.error} context="백테스트 검증에 실패했습니다" />
            )}
          </CardContent>
        </Card>
      )}

      {validation && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="size-4" /> 3. 검증 리포트
            </CardTitle>
            <CardDescription>
              증빙 {validation.backtest_report_id} · 리플레이 신호 {validation.replayed_signals}건 ·
              research_only (라이브 승인 아님)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Object.entries(METRIC_LABELS).map(([key, label]) =>
                key in validation.metrics ? (
                  <div key={key} className="rounded-xl border border-hairline px-3.5 py-3">
                    <p className="text-[12px] text-muted">{label}</p>
                    <p className="text-[15px] font-semibold">
                      {formatMetric(key, validation.metrics[key])}
                    </p>
                  </div>
                ) : null,
              )}
            </div>
            {!ticket && (
              <Button onClick={onCreateTicket} disabled={createTicket.isPending} className="w-fit">
                <ShieldCheck /> {createTicket.isPending ? "티켓 생성 중…" : "전략 승인 티켓 생성 (level_3)"}
              </Button>
            )}
            {createTicket.isError && (
              <ErrorState error={createTicket.error} context="승인 티켓 생성에 실패했습니다" />
            )}
          </CardContent>
        </Card>
      )}

      {ticket && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-4" /> 4. 전략 승인
            </CardTitle>
            <CardDescription>
              전략 단위 승인입니다. 승인하면 이 전략이 감시 상태로 무장되며, 유효기간이 지나면
              자동으로 만료되어 재승인이 필요합니다. 매매 각각의 승인이 아닙니다.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="neutral">ticket: {ticket.ticket_id}</Badge>
              <Badge variant="neutral">자본 한도 {(ticket.capital_budget_pct * 100).toFixed(0)}%</Badge>
              <Badge variant="neutral">{ticket.requested_execution_level}</Badge>
              <Badge variant={ticket.status === "approved" ? "safe" : "warn"}>{ticket.status}</Badge>
              <Badge variant="safe">live_trading_enabled: false</Badge>
            </div>
            {ticket.status === "pending" ? (
              <Button onClick={onApproveTicket} disabled={approveTicket.isPending} className="w-fit">
                <CheckCircle2 /> {approveTicket.isPending ? "승인 중…" : "전략 승인 (arming)"}
              </Button>
            ) : (
              <p className="text-[13px] text-muted">
                승인 완료 — 전략이 무장되었습니다. 셋업이 완성되면 승인 알림(매매 티켓) 경로로
                이어집니다. 유효기간: {new Date(ticket.valid_until).toLocaleString()}
              </p>
            )}
            {approveTicket.isError && (
              <ErrorState error={approveTicket.error} context="전략 승인에 실패했습니다" />
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
