import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Sprout } from "lucide-react";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import type { CultivationCharacter } from "../../api/cultivation";

const LIFECYCLE_VARIANT: Record<string, "success" | "info"> = {
  cultivating: "info",
  ready: "success",
};

/**
 * 角色卡片：身份（终身 ID）+ 来源/生命周期 + 培养进度。
 * 列表页只做导航，细节全部在详情页（履历=证据链）。
 */
export function CharacterCard({ character }: { character: CultivationCharacter }) {
  const { t } = useTranslation();
  const program = character.program;
  const total = program?.stages_total ?? 0;
  const current = program ? Math.min(program.current_stage, total) : 0;

  return (
    <Link
      to={`/cultivation/${character.id}`}
      data-testid={`character-card-${character.id}`}
      className="group flex flex-col gap-3 rounded-2xl border border-border bg-background/55 p-4 transition-colors hover:border-border-active hover:bg-surface-elevated"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Sprout className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{character.name}</p>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {character.identity_id}
          </p>
        </div>
        <Badge variant={LIFECYCLE_VARIANT[character.lifecycle] ?? "muted"}>
          {enumLabel(t, "cultivation:lifecycle", character.lifecycle)}
        </Badge>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="muted">{enumLabel(t, "cultivation:origin", character.origin)}</Badge>
        {program ? (
          <Badge variant="default">{enumLabel(t, "cultivation:template", program.template)}</Badge>
        ) : (
          <Badge variant="muted">{t("cultivation:template.none")}</Badge>
        )}
      </div>

      {program ? (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>
              {program.status === "active"
                ? t("cultivation:detail.stage", { current, total })
                : t("cultivation:detail.stageDone")}
            </span>
            <span className="font-mono">{character.created_at.slice(0, 10)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width]"
              style={{ width: total > 0 ? `${Math.round((current / total) * 100)}%` : "0%" }}
            />
          </div>
        </div>
      ) : (
        <p className="font-mono text-[11px] text-muted-foreground">
          {character.created_at.slice(0, 10)}
        </p>
      )}
    </Link>
  );
}
