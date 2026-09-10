import { useTranslation } from "react-i18next";
import { TraitsList } from "../person/traits-list";
import { CompetencyList } from "../person/competency-list";
import type { CultivationCharacterDetail } from "../../api/cultivation";

/**
 * 成品档案（只读）：人格倾向 + 能力画像 + 履历统计。
 *
 * 能力分只可能来自后端聚合器（证据 → 评估），没有直接写分入口；未评估的维度
 * 显示「未评估」而不是 0 —— 这是能力系统的硬要求（competency-system.md §2）。
 */
export function CharacterProfile({ character }: { character: CultivationCharacterDetail }) {
  const { t } = useTranslation();
  const ready = character.lifecycle === "ready";
  const programSessions = character.programs.reduce(
    (sum, program) => sum + Number(program.resource_used.sessions ?? 0),
    0,
  );
  const programKnowledge = character.programs.reduce(
    (sum, program) => sum + Number(program.resource_used.knowledge ?? 0),
    0,
  );
  // 自由养成没有 program 资源计数：用履历里的自由会话事件补齐，避免显示 0。
  // 模板阶段事件一个事件含多条会话，所以只统计 program_id 为空的事件。
  const freeEvents = character.events.filter((event) => event.program_id === null);
  const freeSessions = freeEvents.filter((event) => event.kind !== "fortune").length;
  const freeKnowledge = freeEvents.reduce(
    (sum, event) => sum + Number(event.outcome.knowledge_produced ?? 0),
    0,
  );
  const sessions = programSessions + freeSessions;
  const knowledge = programKnowledge + freeKnowledge;
  const professional = character.competencies.professional ?? [];

  return (
    <section
      className="space-y-4 rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="character-profile"
    >
      <div>
        <h3 className="text-sm font-semibold">{t("cultivation:profile.title")}</h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {ready ? t("cultivation:profile.readyHint") : t("cultivation:profile.growingHint")}
        </p>
      </div>

      <dl className="grid grid-cols-3 gap-2 text-center">
        {[
          {
            label: t("cultivation:profile.statEvents"),
            value: String(character.events.length),
          },
          { label: t("cultivation:profile.statSessions"), value: String(sessions) },
          {
            label: t("cultivation:profile.statKnowledge"),
            value: String(knowledge),
          },
        ].map((stat) => (
          <div key={stat.label} className="rounded-xl border border-border/60 px-2 py-2">
            <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {stat.label}
            </dt>
            <dd className="mt-0.5 font-mono text-sm font-semibold">{stat.value}</dd>
          </div>
        ))}
      </dl>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("person:traitsTitle")}
        </h4>
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{t("person:traitsHint")}</p>
        <div className="mt-2">
          <TraitsList traits={character.traits} />
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("person:competenciesTitle")}
        </h4>
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
          {t("person:competenciesHint")}
        </p>
        <div className="mt-2">
          <CompetencyList rows={character.competencies.general} />
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("person:professionalTitle")}
        </h4>
        {professional.length === 0 ? (
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
            {t("person:professionalEmpty")}
          </p>
        ) : (
          <div className="mt-2">
            <CompetencyList rows={professional} testId="professional-competency-list" />
          </div>
        )}
      </div>
    </section>
  );
}
