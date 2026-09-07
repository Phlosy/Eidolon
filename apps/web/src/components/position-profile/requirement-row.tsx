import { useTranslation } from "react-i18next";
import type { ProfileRequirement } from "../../types";

/**
 * 能力要求条 + 字段展示（P7）。
 * 屏幕只显示岗位标准（min/target)，**不显示 Employee 当前值** —— P8 才叠加。
 */
export function RequirementBar({
  minimum,
  target,
}: {
  minimum: number | null;
  target: number | null;
}) {
  const marks = [minimum ?? 0, target ?? 100].filter((value) => value > 0 && value <= 100);
  return (
    <div className="relative h-2 w-32 overflow-hidden rounded-full border border-border/60 bg-muted/40">
      {marks.map((value) => (
        <span
          key={value}
          className="absolute inset-y-0 w-px bg-foreground/50"
          style={{ left: `${value}%` }}
          aria-hidden
        />
      ))}
    </div>
  );
}

export function MetroLine({ minimum, target }: { minimum: number | null; target: number | null }) {
  const low = minimum ?? 0;
  const high = target ?? 100;
  const filled = Math.max(0, Math.min(100, high - (low > high ? 0 : low)));
  return (
    <div className="mt-1 h-2 w-32 overflow-hidden rounded-full bg-muted/40">
      <div className="h-full bg-primary/50" style={{ width: `${filled}%` }} />
    </div>
  );
}

/** 一行要求（展示）。 */
export function RequirementRow({ item }: { item: ProfileRequirement }) {
  const { t } = useTranslation();
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border px-3 py-2">
      <div className="min-w-[10rem]">
        <span className="text-sm font-medium">{item.name}</span>
        <span className="ml-2 font-mono text-[11px] text-muted-foreground">{item.code}</span>
        {item.critical ? (
          <span className="ml-2 rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-400">
            {t("position:positions.critical")}
          </span>
        ) : null}
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <MetroLine minimum={item.minimum_score} target={item.target_score} />
        <RequirementBar minimum={item.minimum_score} target={item.target_score} />
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs">
        <span>
          {t("position:positions.required")}: {item.requirement_type}
        </span>
        <span>
          {t("position:positions.minimum")}: {item.minimum_score ?? "—"}
        </span>
        <span>
          {t("position:positions.target")}: {item.target_score ?? "—"}
        </span>
        {item.minimum_confidence !== null ? (
          <span>
            {t("position:positions.confidence")}: {Math.round((item.minimum_confidence ?? 0) * 100)}
            %
          </span>
        ) : null}
        <span>
          {t("position:positions.weight")}: {Math.round((item.weight ?? 0) * 100)}%
        </span>
      </div>
    </li>
  );
}
