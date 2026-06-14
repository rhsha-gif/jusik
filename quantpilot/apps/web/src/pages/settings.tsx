import { useState } from "react";
import { ExternalLink, RotateCcw, Wifi } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { JsonViewer } from "@/components/json-viewer";
import { PageHeader } from "@/components/page-header";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { getApiBase, resetApiBase, setApiBase } from "@/lib/api";
import { clearActivity } from "@/lib/activity-log";
import { useHealth } from "@/lib/queries";
import { clearWorkingPolicy, useWorkingPolicy } from "@/lib/working-policy";

const SAFETY_DEFAULTS = [
  "LIVE_TRADING_ENABLED=false",
  "BROKER_MODE=mock",
  "DEFAULT_ORDER_TYPE=limit",
  "MARKET_ORDERS_ENABLED=false",
  "DATA_MODE=fixture",
];

export function SettingsPage() {
  const health = useHealth();
  const workingPolicy = useWorkingPolicy();
  const [apiBaseInput, setApiBaseInput] = useState(getApiBase());
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setApiBase(apiBaseInput);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    void health.refetch();
  };

  const handleResetAll = () => {
    resetApiBase();
    clearWorkingPolicy();
    clearActivity();
    setApiBaseInput(getApiBase());
    void health.refetch();
  };

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="설정 & 안전"
        description="로컬 UI 설정과 백엔드 연결을 관리합니다. 브로커 자격증명은 이 프리하니스의 범위 밖이며 입력란도 제공하지 않습니다."
      />

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>백엔드 연결</CardTitle>
            <CardDescription>API Base URL — 로컬 프리하니스 주소만 사용하세요.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="api-base">API Base URL</Label>
              <div className="flex gap-2">
                <Input
                  id="api-base"
                  value={apiBaseInput}
                  onChange={(event) => setApiBaseInput(event.target.value)}
                  className="font-mono text-[13px]"
                />
                <Button variant="secondary" onClick={handleSave}>
                  {saved ? "저장됨" : "저장"}
                </Button>
              </div>
            </div>
            <div className="flex items-center gap-2.5">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void health.refetch()}
                disabled={health.isFetching}
              >
                <Wifi /> 연결 테스트
              </Button>
              {health.isSuccess && <Badge variant="safe">연결됨 · {health.data.status}</Badge>}
              {health.isError && <Badge variant="danger">연결 실패</Badge>}
              {health.isFetching && <Badge variant="neutral">확인 중…</Badge>}
            </div>
            {health.isSuccess && <JsonViewer data={health.data} title="Raw JSON (health)" />}
            <a
              href={`${getApiBase()}/docs`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-muted hover:text-accent"
            >
              <ExternalLink className="size-3.5" /> 개발자용 API 문서 (Swagger) 열기
            </a>
          </CardContent>
        </Card>

        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <CardTitle>안전 기본값 (읽기 전용)</CardTitle>
              <CardDescription>
                프리하니스가 강제하는 값으로, UI에서 변경할 수 없습니다.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="flex flex-col gap-2">
                {SAFETY_DEFAULTS.map((item) => (
                  <li
                    key={item}
                    className="rounded-xl border border-hairline bg-surface-raised px-3.5 py-2.5 font-mono text-[12.5px] shadow-sm"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>테마</CardTitle>
              <CardDescription>시스템 / 라이트 / 다크</CardDescription>
            </CardHeader>
            <CardContent>
              <ThemeToggle />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>로컬 UI 상태 초기화</CardTitle>
              <CardDescription>
                작업 정책 핀{workingPolicy ? ` (${workingPolicy.policyId})` : ""}, 요청 기록,
                API Base 설정을 초기화합니다. 백엔드 데이터는 변경되지 않습니다.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="danger" size="sm" onClick={handleResetAll}>
                <RotateCcw /> 전체 초기화
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
