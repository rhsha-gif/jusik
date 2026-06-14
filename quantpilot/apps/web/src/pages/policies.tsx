import { useState } from "react";
import { CheckCircle2, CircleDashed, ListChecks, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { JsonViewer } from "@/components/json-viewer";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import {
  DEFAULT_POLICY_TEXT,
  usePolicyConfirm,
  usePolicyParse,
  usePolicyPreview,
} from "@/lib/queries";
import { markWorkingPolicyConfirmed, setWorkingPolicy } from "@/lib/working-policy";
import type { UserPolicy } from "@/lib/types";
import { formatKRW, formatPercent } from "@/lib/utils";

const SAFETY_CHECKLIST = [
  "모의 브로커(Mock Broker)만 사용",
  "시장가 주문 비활성",
  "지정가 주문만 허용",
  "실거래 자격증명 요청 없음",
  "브로커 API 키 저장 없음",
];

export function PoliciesPage() {
  const [text, setText] = useState(DEFAULT_POLICY_TEXT);
  const preview = usePolicyPreview();
  const parse = usePolicyParse();
  const confirm = usePolicyConfirm();

  const parsedPolicy = parse.data ?? null;
  const previewPolicy = preview.data?.policy ?? null;
  const shownPolicy = parsedPolicy ?? previewPolicy;
  const confirmReady = parsedPolicy != null && preview.isSuccess;

  const handlePreview = () => preview.mutate({ text, user_id: "fixture-user" });
  const handleParse = () =>
    parse.mutate(
      { text, user_id: "fixture-user" },
      { onSuccess: (policy) => setWorkingPolicy(policy, false) },
    );
  const handleConfirm = () => {
    if (!parsedPolicy) return;
    confirm.mutate(parsedPolicy.policy_id, {
      onSuccess: (policy) => markWorkingPolicyConfirmed(policy.policy_id),
    });
  };

  return (
    <>
      <PageHeader
        eyebrow="Policy Studio"
        title="정책 스튜디오"
        description="자연어 정책 텍스트를 구조화된 정책으로 파싱하고, 미리보기로 검증한 뒤 확정합니다. 확정은 로컬 프리하니스에만 적용됩니다."
      />

      <div className="grid items-start gap-5 xl:grid-cols-[1.1fr_1.4fr_0.9fr]">
        {/* Left: editor */}
        <Card>
          <CardHeader>
            <CardTitle>1. 정책 작성</CardTitle>
            <CardDescription>자연어 또는 키워드로 정책을 기술합니다.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="policy-text">정책 텍스트</Label>
              <Textarea
                id="policy-text"
                rows={7}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
              <p className="text-[12px] leading-relaxed text-muted">
                예: “KR stock conservative risk monthly rebalance, 현금 30%, 종목당 10%” —
                시장·위험성향·리밸런스 주기·현금 비중 키워드를 인식합니다.
              </p>
            </div>
            <div className="flex flex-wrap gap-2.5">
              <Button variant="secondary" onClick={handlePreview} disabled={preview.isPending}>
                {preview.isPending ? "미리보기 중…" : "정책 미리보기"}
              </Button>
              <Button onClick={handleParse} disabled={parse.isPending}>
                {parse.isPending ? "파싱 중…" : "정책 파싱 (저장)"}
              </Button>
            </div>
            <StepIndicator
              previewDone={preview.isSuccess}
              parseDone={parse.isSuccess}
              confirmDone={confirm.isSuccess}
            />
          </CardContent>
        </Card>

        {/* Center: result */}
        <div className="flex flex-col gap-5">
          {(preview.isError || parse.isError) && (
            <ErrorState
              error={preview.error ?? parse.error}
              context="정책 처리에 실패했습니다"
            />
          )}
          {shownPolicy ? (
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-3">
                <div>
                  <CardTitle>파싱 결과</CardTitle>
                  <CardDescription>
                    <code className="font-mono">{shownPolicy.policy_id}</code>
                    {parsedPolicy ? " · 저장됨" : " · 미리보기 (저장 안 됨)"}
                  </CardDescription>
                </div>
                <Badge variant={confirm.isSuccess ? "safe" : parsedPolicy ? "accent" : "neutral"}>
                  {confirm.isSuccess ? "확정됨" : parsedPolicy ? "파싱됨" : "미리보기"}
                </Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <PolicySummary policy={shownPolicy} />
                <JsonViewer data={shownPolicy} title="Raw JSON (policy)" />
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="p-8 text-center text-[13px] text-muted">
                미리보기 또는 파싱을 실행하면 구조화된 정책이 여기에 표시됩니다.
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: safety checklist + confirm */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-4.5 text-safe" /> 안전 체크리스트
            </CardTitle>
            <CardDescription>확정 전 자동 보장 항목</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <ul className="flex flex-col gap-2">
              {SAFETY_CHECKLIST.map((item) => (
                <li key={item} className="flex items-center gap-2 text-[13px]">
                  <CheckCircle2 className="size-4 shrink-0 text-safe" />
                  {item}
                </li>
              ))}
            </ul>

            <Dialog>
              <DialogTrigger asChild>
                <Button className="w-full" disabled={!confirmReady || confirm.isPending}>
                  <ListChecks /> 정책 확정
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogTitle>정책을 확정할까요?</DialogTitle>
                <DialogDescription>
                  이 확정은 로컬 프리하니스에만 적용됩니다. 실제 증권사 주문을 내지 않습니다.
                </DialogDescription>
                {parsedPolicy && (
                  <p className="mt-3 rounded-xl border border-hairline bg-surface px-3.5 py-2.5 font-mono text-[12px]">
                    POST /api/policies/confirm · {parsedPolicy.policy_id}
                  </p>
                )}
                <div className="mt-5 flex justify-end gap-2.5">
                  <DialogClose asChild>
                    <Button variant="ghost">취소</Button>
                  </DialogClose>
                  <DialogClose asChild>
                    <Button onClick={handleConfirm}>확정</Button>
                  </DialogClose>
                </div>
              </DialogContent>
            </Dialog>

            {!confirmReady && (
              <p className="text-[12px] leading-relaxed text-muted">
                확정 버튼은 미리보기와 파싱이 모두 성공해야 활성화됩니다.
              </p>
            )}
            {confirm.isSuccess && (
              <p className="text-[12.5px] font-medium text-safe" role="status">
                정책이 확정되었습니다. 신호 보드와 Level 1-2 실행에서 이 정책이 사용됩니다.
              </p>
            )}
            {confirm.isError && (
              <p className="text-[12.5px] text-danger" role="alert">
                확정 실패: {confirm.error instanceof Error ? confirm.error.message : "오류"}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function StepIndicator({
  previewDone,
  parseDone,
  confirmDone,
}: {
  previewDone: boolean;
  parseDone: boolean;
  confirmDone: boolean;
}) {
  const steps = [
    { label: "미리보기", done: previewDone },
    { label: "파싱", done: parseDone },
    { label: "확정", done: confirmDone },
  ];
  return (
    <ol className="flex items-center gap-3" aria-label="정책 진행 단계">
      {steps.map((step, index) => (
        <li key={step.label} className="flex items-center gap-1.5 text-[12px]">
          {step.done ? (
            <CheckCircle2 className="size-4 text-safe" />
          ) : (
            <CircleDashed className="size-4 text-muted" />
          )}
          <span className={step.done ? "font-medium text-ink" : "text-muted"}>
            {index + 1}. {step.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function PolicySummary({ policy }: { policy: UserPolicy }) {
  const rows: { label: string; value: string }[] = [
    { label: "시장", value: policy.market },
    { label: "위험 성향", value: policy.risk_profile },
    { label: "실행 모드", value: policy.execution_mode },
    { label: "브로커", value: policy.broker },
    { label: "허용 주문 유형", value: policy.allowed_order_types.join(", ") },
    { label: "리밸런스 주기", value: policy.rebalance_frequency },
    { label: "최대 보유 종목", value: String(policy.max_positions) },
    { label: "종목당 최대 비중", value: formatPercent(policy.max_position_weight, 0) },
    { label: "섹터 최대 비중", value: formatPercent(policy.max_sector_weight, 0) },
    { label: "최소 현금 비중", value: formatPercent(policy.min_cash_weight, 0) },
    { label: "일일 손실 한도", value: formatPercent(policy.daily_loss_limit, 0) },
    { label: "월간 손실 한도", value: formatPercent(policy.monthly_loss_limit, 0) },
    { label: "일일 최대 주문", value: `${policy.max_daily_orders}건` },
    { label: "단일 주문 한도", value: formatKRW(policy.single_order_cash_limit) },
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-5 gap-y-2.5 text-[13px] md:grid-cols-3">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-[11.5px] text-muted">{row.label}</dt>
          <dd className="mt-0.5 font-medium tabular-nums">{row.value}</dd>
        </div>
      ))}
      {policy.preferred_themes.length > 0 && (
        <div className="col-span-full">
          <dt className="text-[11.5px] text-muted">선호 테마</dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {policy.preferred_themes.map((theme) => (
              <Badge key={theme} variant="accent">
                {theme}
              </Badge>
            ))}
          </dd>
        </div>
      )}
      {policy.blocklist.length > 0 && (
        <div className="col-span-full">
          <dt className="text-[11.5px] text-muted">제외 종목</dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {policy.blocklist.map((ticker) => (
              <Badge key={ticker} variant="danger">
                {ticker}
              </Badge>
            ))}
          </dd>
        </div>
      )}
    </dl>
  );
}
