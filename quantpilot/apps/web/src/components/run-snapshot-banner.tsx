import { AlertTriangle, RefreshCw, RotateCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { RunSnapshot } from "@/lib/run-store";
import { cn, formatTime } from "@/lib/utils";

export function RunSnapshotBanner({
  snapshot,
  workingPolicyId,
  onRerun,
  rerunning,
  rerunLabel = "Rerun",
}: {
  snapshot: RunSnapshot;
  workingPolicyId?: string | null;
  onRerun: () => void;
  rerunning: boolean;
  rerunLabel?: string;
}) {
  const policyMismatch = Boolean(workingPolicyId && workingPolicyId !== snapshot.policyId);
  const levelSummary = snapshot.level12?.daily_report.summary;
  const operatorStatus = snapshot.operator?.status;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-card border bg-surface-solid px-4 py-3 text-[13px] sm:flex-row sm:items-center sm:justify-between",
        policyMismatch ? "border-warn/40" : "border-hairline",
      )}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={policyMismatch ? "warn" : "accent"}>
            {policyMismatch ? <AlertTriangle /> : <RotateCw />}
            snapshot
          </Badge>
          <Badge variant="neutral">policy {snapshot.policyId}</Badge>
          {snapshot.policyVersion != null && <Badge variant="neutral">v{snapshot.policyVersion}</Badge>}
          {snapshot.source && <Badge variant="neutral">{snapshot.source}</Badge>}
          <Badge variant="safe">live false</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted">
          <span>saved {formatTime(snapshot.savedAt)}</span>
          {levelSummary?.candidate_count != null && <span>{Number(levelSummary.candidate_count)} candidates</span>}
          {levelSummary?.signal_count != null && <span>{Number(levelSummary.signal_count)} signals</span>}
          {operatorStatus && <span>operator {operatorStatus}</span>}
          {policyMismatch && <span className="text-warn">pinned policy differs</span>}
        </div>
      </div>
      <Button variant="secondary" size="sm" onClick={onRerun} disabled={rerunning}>
        <RefreshCw className={rerunning ? "animate-spin" : ""} />
        {rerunning ? "Running" : rerunLabel}
      </Button>
    </div>
  );
}
