import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import type { Skill } from "../../types";
import { formatPercent } from "../../utils/format";
import { ProgressTrack } from "../game/progress-track";
import { EmptyState } from "../common/states";

export function EmployeeSkillPanel({ skills }: { skills: Skill[] }) {
  const { t } = useTranslation();
  if (!skills.length) return <EmptyState title={t("employee:skills.emptyTitle")} />;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {skills.map((skill) => {
        const score = Math.round((skill.success_rate ?? 0) * 100);
        return (
          <article key={skill.id} className="rounded-2xl border border-border bg-background/45 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-status-learning/20 bg-status-learning/8 text-status-learning">
                  <Sparkles className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">{skill.name}</p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    {t(`employee:skills.validation.${skill.validation_status}`)} · v{skill.version}
                  </p>
                </div>
              </div>
              <span className="type-telemetry text-xl font-semibold">{score}</span>
            </div>
            <ProgressTrack
              value={score}
              tone={score >= 75 ? "success" : "primary"}
              className="mt-4"
            />
            <div className="mt-3 flex justify-between text-[10px] text-muted-foreground">
              <span>
                {t("employee:skills.table.attempts")}: {skill.attempts}
              </span>
              <span>
                {t("employee:skills.table.successRate")}: {formatPercent(skill.success_rate)}
              </span>
            </div>
          </article>
        );
      })}
    </div>
  );
}
