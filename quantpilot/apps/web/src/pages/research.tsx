import { useState } from "react";
import { FlaskConical, Microscope } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { JsonViewer } from "@/components/json-viewer";
import { EmptyState, ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { DEFAULT_POLICY_TEXT, useResearchAnalyst, useResearchUniverse } from "@/lib/queries";
import { useWorkingPolicy } from "@/lib/working-policy";
import type { AnalystReport, CandidateUniverseItem } from "@/lib/types";
import { formatPercent } from "@/lib/utils";

const RATING_VARIANT: Record<AnalystReport["rating"], "safe" | "neutral" | "warn" | "danger"> = {
  positive: "safe",
  neutral: "neutral",
  caution: "warn",
  blocked: "danger",
};

const RATING_LABEL: Record<AnalystReport["rating"], string> = {
  positive: "긍정",
  neutral: "중립",
  caution: "주의",
  blocked: "차단",
};

function PolicyInputPanel({
  text,
  onTextChange,
  usePinned,
  onUsePinnedChange,
}: {
  text: string;
  onTextChange: (value: string) => void;
  usePinned: boolean;
  onUsePinnedChange: (value: boolean) => void;
}) {
  const workingPolicy = useWorkingPolicy();
  return (
    <div className="flex flex-col gap-3">
      {workingPolicy && (
        <label className="flex cursor-pointer items-center gap-2.5 rounded-xl border border-hairline bg-surface-solid px-3.5 py-2.5 text-[13px]">
          <input
            type="checkbox"
            checked={usePinned}
            onChange={(event) => onUsePinnedChange(event.target.checked)}
            className="accent-(--qp-accent)"
          />
          <span>
            작업 정책 사용{" "}
            <code className="font-mono text-[11.5px] text-muted">{workingPolicy.policyId}</code>
          </span>
          <Badge variant={workingPolicy.confirmed ? "safe" : "warn"}>
            {workingPolicy.confirmed ? "확정됨" : "미확정"}
          </Badge>
        </label>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="research-policy-text">정책 텍스트 (자연어)</Label>
        <Textarea
          id="research-policy-text"
          rows={3}
          value={text}
          onChange={(event) => onTextChange(event.target.value)}
          disabled={usePinned && workingPolicy != null}
          placeholder={DEFAULT_POLICY_TEXT}
        />
        <p className="text-[12px] text-muted">
          작업 정책을 사용하지 않으면 위 텍스트가 새 정책으로 파싱됩니다. 백엔드 파서는 시장·위험
          성향·리밸런스 주기·테마 키워드를 인식합니다.
        </p>
      </div>
    </div>
  );
}

export function ResearchPage() {
  const workingPolicy = useWorkingPolicy();
  const [text, setText] = useState(DEFAULT_POLICY_TEXT);
  const [usePinned, setUsePinned] = useState(true);
  const universe = useResearchUniverse();
  const analyst = useResearchAnalyst();

  const requestBody = () =>
    usePinned && workingPolicy
      ? { policy_id: workingPolicy.policyId }
      : { text, user_id: "fixture-user" };

  return (
    <>
      <PageHeader
        title="리서치"
        description="정책 기반 후보 유니버스와 fixture 기반 애널리스트 리포트를 생성합니다. 모든 값은 모의 데이터이며 실제 시세가 아닙니다."
      />

      <Tabs defaultValue="universe">
        <TabsList>
          <TabsTrigger value="universe">
            <FlaskConical className="mr-1.5 size-3.5" /> 유니버스
          </TabsTrigger>
          <TabsTrigger value="analyst">
            <Microscope className="mr-1.5 size-3.5" /> 애널리스트 리포트
          </TabsTrigger>
        </TabsList>

        <TabsContent value="universe" className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <CardTitle>후보 유니버스 생성</CardTitle>
              <CardDescription>POST /api/research/universe — fixture 종목 풀에서 정책 필터를 적용합니다.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <PolicyInputPanel
                text={text}
                onTextChange={setText}
                usePinned={usePinned}
                onUsePinnedChange={setUsePinned}
              />
              <div>
                <Button onClick={() => universe.mutate(requestBody())} disabled={universe.isPending}>
                  {universe.isPending ? "생성 중…" : "유니버스 생성"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {universe.isError && (
            <ErrorState
              error={universe.error}
              context="유니버스 생성에 실패했습니다"
              onRetry={() => universe.mutate(requestBody())}
            />
          )}

          {universe.isSuccess && (
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <div>
                  <CardTitle>후보 종목 {universe.data.candidates.length}개</CardTitle>
                  <CardDescription>
                    policy: <code className="font-mono">{universe.data.policy_id}</code> · Fixture
                    데이터
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {universe.data.candidates.length === 0 ? (
                  <EmptyState
                    title="조건을 만족하는 후보가 없습니다"
                    description="정책의 테마/시장 조건을 완화해 다시 시도해 보세요."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-left text-[13px]">
                      <thead>
                        <tr className="border-b border-hairline text-[12px] text-muted">
                          <th className="py-2.5 pr-4 font-medium">종목</th>
                          <th className="py-2.5 pr-4 font-medium">섹터</th>
                          <th className="py-2.5 pr-4 font-medium">적격성</th>
                          <th className="py-2.5 font-medium">비고</th>
                        </tr>
                      </thead>
                      <tbody>
                        {universe.data.candidates.map((item) => (
                          <UniverseRow key={item.ticker} item={item} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <JsonViewer data={universe.data} title="Raw JSON (universe)" />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="analyst" className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <CardTitle>애널리스트 리포트 요청</CardTitle>
              <CardDescription>
                POST /api/research/analyst — 유니버스의 각 후보에 대해 fixture 기반 리포트를
                생성합니다.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <PolicyInputPanel
                text={text}
                onTextChange={setText}
                usePinned={usePinned}
                onUsePinnedChange={setUsePinned}
              />
              <div>
                <Button onClick={() => analyst.mutate(requestBody())} disabled={analyst.isPending}>
                  {analyst.isPending ? "생성 중…" : "리포트 생성"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {analyst.isError && (
            <ErrorState
              error={analyst.error}
              context="리포트 생성에 실패했습니다"
              onRetry={() => analyst.mutate(requestBody())}
            />
          )}

          {analyst.isSuccess && (
            <div className="flex flex-col gap-4">
              {analyst.data.analyst_reports.map((report) => (
                <AnalystReportCard key={report.ticker} report={report} />
              ))}
              <JsonViewer data={analyst.data} title="Raw JSON (analyst)" />
            </div>
          )}
        </TabsContent>
      </Tabs>
    </>
  );
}

function UniverseRow({ item }: { item: CandidateUniverseItem }) {
  return (
    <tr className="h-12 border-b border-hairline/60">
      <td className="pr-4">
        <span className="font-mono text-[13px] font-semibold">{item.ticker}</span>
        <span className="ml-2 text-muted">{item.name}</span>
      </td>
      <td className="pr-4 text-muted">{item.sector}</td>
      <td className="pr-4">
        <span className="flex flex-wrap gap-1.5">
          <Badge variant={item.theme_match ? "safe" : "neutral"}>테마</Badge>
          <Badge variant={item.liquidity_pass ? "safe" : "warn"}>유동성</Badge>
          <Badge variant={item.data_ready ? "safe" : "warn"}>데이터</Badge>
          {item.analyst_required && <Badge variant="accent">리포트 필요</Badge>}
        </span>
      </td>
      <td className="text-[12.5px] text-muted">
        {item.block_reason ? (
          <span className="text-danger">{item.block_reason}</span>
        ) : (
          "통과"
        )}
      </td>
    </tr>
  );
}

function AnalystReportCard({ report }: { report: AnalystReport }) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="font-mono">{report.ticker}</CardTitle>
          <CardDescription>
            기준일 {report.data_as_of} · 신뢰도 {formatPercent(report.confidence, 0)} · Fixture
            데이터
          </CardDescription>
        </div>
        <Badge variant={RATING_VARIANT[report.rating]}>{RATING_LABEL[report.rating]}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-[13.5px] leading-relaxed">{report.summary}</p>
        <div className="grid gap-4 md:grid-cols-2">
          <ReportList title="투자 논거" items={report.investment_thesis} />
          <ReportList title="촉매" items={report.catalysts} />
        </div>
        <div className="grid gap-3 rounded-xl border border-hairline bg-surface-solid p-4 text-[13px] md:grid-cols-3">
          <div>
            <p className="text-[11.5px] font-medium text-muted">밸류에이션</p>
            <p className="mt-1 leading-relaxed">{report.valuation_view}</p>
          </div>
          <div>
            <p className="text-[11.5px] font-medium text-muted">기술적 관점</p>
            <p className="mt-1 leading-relaxed">{report.technical_view}</p>
          </div>
          <div>
            <p className="text-[11.5px] font-medium text-muted">운영 관점</p>
            <p className="mt-1 leading-relaxed">{report.operation_view}</p>
          </div>
        </div>
        {report.watch_conditions.length > 0 && (
          <ReportList title="관찰 조건" items={report.watch_conditions} />
        )}
        <JsonViewer data={report} title={`Raw JSON (${report.ticker})`} />
      </CardContent>
    </Card>
  );
}

function ReportList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-[11.5px] font-medium text-muted">{title}</p>
      <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-4 text-[13px] leading-relaxed">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
