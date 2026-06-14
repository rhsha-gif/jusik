import { CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useWorkingPolicy } from "@/lib/working-policy";
import { cn } from "@/lib/utils";

export function ActivePolicyChip({ className }: { className?: string }) {
  const policy = useWorkingPolicy();
  if (!policy) {
    return (
      <div className={cn("flex flex-wrap items-center gap-2", className)}>
        <Badge variant="neutral">
          <CircleDashed />
          no pinned policy
        </Badge>
        <Badge variant="safe">
          <ShieldCheck />
          broker mock
        </Badge>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <Badge variant={policy.confirmed ? "safe" : "accent"}>
        {policy.confirmed ? <CheckCircle2 /> : <CircleDashed />}
        policy v{policy.version}
      </Badge>
      <Badge variant="neutral">{policy.market}</Badge>
      <Badge variant="neutral">{policy.riskProfile}</Badge>
      <Badge variant="safe">broker {policy.broker}</Badge>
      {policy.direction && <Badge variant="accent">{policy.direction}</Badge>}
    </div>
  );
}
