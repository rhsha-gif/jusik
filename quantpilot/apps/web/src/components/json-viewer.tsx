import { useState } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

export function CopyButton({ text, label = "복사" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-hairline bg-surface-solid px-3 py-1 text-[12px] font-medium text-muted hover:bg-accent-soft hover:text-accent"
    >
      {copied ? <Check className="size-3 text-safe" /> : <Copy className="size-3" />}
      {copied ? "복사됨" : label}
    </button>
  );
}

export function JsonViewer({
  data,
  title = "Raw JSON",
  defaultOpen = false,
  className,
}: {
  data: unknown;
  title?: string;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const json = JSON.stringify(data, null, 2);

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn("rounded-xl border border-hairline bg-surface-solid", className)}
    >
      <div className="flex items-center justify-between px-4 py-2.5">
        <CollapsibleTrigger className="flex cursor-pointer items-center gap-2 text-[13px] font-medium text-muted hover:text-ink">
          <ChevronDown
            className={cn("size-4 transition-transform", open ? "" : "-rotate-90")}
          />
          {title}
        </CollapsibleTrigger>
        <CopyButton text={json} label="JSON 복사" />
      </div>
      <CollapsibleContent>
        <pre className="max-h-96 overflow-auto border-t border-hairline px-4 py-3 font-mono text-[12.5px] leading-relaxed text-ink">
          {json}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
