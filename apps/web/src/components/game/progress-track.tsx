import { cn } from "../../utils/cn";

export function ProgressTrack({
  value,
  tone = "primary",
  className,
}: {
  value: number;
  tone?: "primary" | "success" | "warning" | "danger";
  className?: string;
}) {
  const clamped = Math.min(100, Math.max(0, value));
  const tones = {
    primary: "bg-primary",
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-danger",
  } as const;
  return (
    <div
      className={cn("h-1.5 overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
    >
      <span
        className={cn("block h-full rounded-full transition-[width] duration-500", tones[tone])}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function LevelBadge({ level, label }: { level: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-lg border border-primary/25 bg-primary/10 px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-primary">
      {label} {String(level).padStart(2, "0")}
    </span>
  );
}
