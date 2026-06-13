import { Monitor, Moon, Sun } from "lucide-react";
import { setThemePreference, useThemePreference, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "시스템 테마", icon: Monitor },
  { value: "light", label: "라이트 테마", icon: Sun },
  { value: "dark", label: "다크 테마", icon: Moon },
];

export function ThemeToggle() {
  const preference = useThemePreference();
  return (
    <div
      role="radiogroup"
      aria-label="테마 설정"
      className="flex items-center rounded-full border border-hairline bg-surface-solid p-0.5"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={label}
          title={label}
          onClick={() => setThemePreference(value)}
          className={cn(
            "flex size-7 cursor-pointer items-center justify-center rounded-full text-muted transition-colors",
            preference === value && "bg-accent-soft text-accent",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
