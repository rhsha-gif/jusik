import { useMemo, useState } from "react";
import { Search, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { JsonViewer, CopyButton } from "@/components/json-viewer";
import { EmptyState } from "@/components/states";
import { PageHeader } from "@/components/page-header";
import {
  buildTroubleshootingBundle,
  clearActivity,
  useActivityLog,
  type ActivityEntry,
} from "@/lib/activity-log";
import { formatDuration, formatTime } from "@/lib/utils";

export function JobsPage() {
  const entries = useActivityLog();
  const [search, setSearch] = useState("");

  const filtered = useMemo(
    () =>
      entries.filter((entry) =>
        search
          ? entry.path.toLowerCase().includes(search.toLowerCase()) ||
            entry.id.includes(search)
          : true,
      ),
    [entries, search],
  );

  return (
    <>
      <PageHeader
        title="작업 & 로그"
        description="이 세션에서 UI가 보낸 모든 API 요청의 기록입니다. 백엔드는 동기식으로 응답하므로 별도 작업 큐 없이 요청 단위로 기록됩니다."
        actions={
          entries.length > 0 && (
            <Button variant="ghost" size="sm" onClick={clearActivity}>
              <Trash2 /> 기록 지우기
            </Button>
          )
        }
      />

      <div className="relative max-w-xs">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
        <Input
          aria-label="엔드포인트 검색"
          placeholder="엔드포인트 또는 ID 검색"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="pl-9"
        />
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title="기록이 없습니다"
          description="다른 페이지에서 요청을 실행하면 여기에서 요청·응답·소요 시간을 확인할 수 있습니다."
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {filtered.map((entry) => (
            <ActivityRow key={entry.id} entry={entry} />
          ))}
        </ul>
      )}
    </>
  );
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  return (
    <li className="rounded-card border border-hairline bg-surface p-4 shadow-card backdrop-blur-xl">
      <div className="flex flex-wrap items-center gap-2.5">
        <Badge variant={entry.ok ? "safe" : entry.status === null ? "warn" : "danger"}>
          {entry.status === null ? "연결 실패" : `HTTP ${entry.status}`}
        </Badge>
        <code className="font-mono text-[13px] font-medium">
          {entry.method} {entry.path}
        </code>
        <span className="ml-auto flex items-center gap-3 text-[12px] tabular-nums text-muted">
          <span>{formatTime(entry.startedAt)}</span>
          <span>{formatDuration(entry.durationMs)}</span>
        </span>
      </div>
      {entry.errorMessage && (
        <p className="mt-2 text-[12.5px] text-danger" role="alert">
          {entry.errorMessage}
        </p>
      )}
      <div className="mt-3 flex flex-col gap-2">
        {entry.requestBody != null && (
          <JsonViewer data={entry.requestBody} title="요청 본문" />
        )}
        {entry.responseBody != null && (
          <JsonViewer data={entry.responseBody} title="응답 본문" />
        )}
        <div>
          <CopyButton
            text={buildTroubleshootingBundle(entry)}
            label="트러블슈팅 번들 복사"
          />
        </div>
      </div>
    </li>
  );
}
