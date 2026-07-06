import { Newspaper, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import { useDailyBriefing } from "@/lib/queries";

export function BriefingPage() {
  const briefing = useDailyBriefing();
  const cards = briefing.data ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Briefing"
        title="데일리 브리핑"
        description="주요 금융 뉴스와 애널리스트 분석을 선별해 보여주는 읽기 전용 화면입니다. 브리핑은 매매 신호 입력이 아니며(격리 경계가 테스트로 강제됨), 판단 참고용으로만 제공됩니다. 현재는 fixture 데이터 — 실제 수집기는 후속 단계입니다."
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void briefing.refetch()}
            disabled={briefing.isFetching}
          >
            <RefreshCw className={briefing.isFetching ? "animate-spin" : ""} /> 새로고침
          </Button>
        }
      />

      {briefing.isError && <ErrorState error={briefing.error} context="브리핑을 불러오지 못했습니다" />}

      {cards.map((card) => (
        <Card key={card.card_id}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Newspaper className="size-4" /> {card.headline}
            </CardTitle>
            <CardDescription>
              {card.source} · {new Date(card.published_at).toLocaleString()}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-[13.5px] leading-relaxed">{card.summary}</p>
            <div className="flex flex-wrap items-center gap-2">
              {card.related_symbols.map((symbol) => (
                <Badge key={symbol} variant="neutral">
                  {symbol}
                </Badge>
              ))}
              {card.tags.map((tag) => (
                <Badge key={tag} variant="neutral">
                  #{tag}
                </Badge>
              ))}
              <Badge variant="safe">신호 입력 아님</Badge>
            </div>
          </CardContent>
        </Card>
      ))}
    </>
  );
}
