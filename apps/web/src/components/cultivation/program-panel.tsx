import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { enumLabel } from "../../utils/labels";
import { useAdvanceCultivationProgram } from "../../hooks/useCultivation";
import type { CultivationProgram } from "../../api/cultivation";

/**
 * 培养实例面板：阶段进度 + 资源消耗 + 推进。
 *
 * 「推进一阶段」是模板路径唯一的写入入口：后端会采样主题、产出知识与教育证据、
 * 触发际遇与评估节点。前端不做任何结果预测（模板只给倾向，结果由 RNG + 际遇决定）。
 */
export function ProgramPanel({ program }: { program: CultivationProgram }) {
  const { t } = useTranslation();
  const advance = useAdvanceCultivationProgram();
  const total = program.stages_total;
  const current = Math.min(program.current_stage, total);
  const done = program.status !== "active";
  const sessions = Number(program.resource_used.sessions ?? 0);
  const knowledge = Number(program.resource_used.knowledge ?? 0);

  return (
    <section
      className="rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid={`program-panel-${program.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t("cultivation:detail.programsTitle")}</h3>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="default">
              {enumLabel(t, "cultivation:template", program.template)}
            </Badge>
            <span>
              {done
                ? t("cultivation:detail.stageDone")
                : t("cultivation:detail.stage", { current, total })}
            </span>
          </p>
        </div>
        {done ? (
          <Badge variant="success">{t("cultivation:detail.programStatus.completed")}</Badge>
        ) : (
          <Badge variant="info">{t("cultivation:detail.programStatus.active")}</Badge>
        )}
      </div>

      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: total > 0 ? `${Math.round((current / total) * 100)}%` : "0%" }}
        />
      </div>

      <p className="mt-2 font-mono text-[11px] text-muted-foreground">
        {t("cultivation:detail.resources", { sessions, knowledge })}
      </p>

      {!done ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            data-testid="advance-program"
            disabled={advance.isPending}
            onClick={() => advance.mutate(program.id)}
          >
            {advance.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            {advance.isPending
              ? t("cultivation:detail.advancing")
              : t("cultivation:detail.advance")}
          </Button>
          <p className="max-w-md text-[11px] leading-4 text-muted-foreground">
            {t("cultivation:detail.advanceHint")}
          </p>
        </div>
      ) : null}

      {advance.isError ? (
        <p className="mt-2 text-xs text-danger" role="alert">
          {advance.error.message}
        </p>
      ) : null}
    </section>
  );
}
