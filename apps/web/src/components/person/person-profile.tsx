import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { TraitsList } from "./traits-list";
import { CompetencyList } from "./competency-list";
import { enumLabel } from "../../utils/labels";
import type { PersonProfile as PersonProfileModel } from "../../api/persons";

/**
 * PersonProfile —— 统一人员档案骨架（T2.1，设计 §9）。
 *
 * 培养 UI / 市场 UI / 员工 UI **共用同一投影与同一渲染**：identity + traits +
 * competencies + knowledge summary；timeline / evidence 由调用方以 `slots` 注入
 * （各自有分页与下钻交互，不塞进本组件）。
 *
 * 语义铁律：未评估 = 「未评估」（不是 0）；score 与 confidence 并列；知识只显示统计。
 */
export function PersonProfile({
  profile,
  slots,
  compact = false,
}: {
  profile: PersonProfileModel;
  slots?: { timeline?: React.ReactNode; evidence?: React.ReactNode };
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const { identity, traits, competencies, knowledge_summary: knowledge } = profile;
  const professional = competencies.professional ?? [];

  return (
    <div className="space-y-4" data-testid="person-profile">
      <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">{identity.name}</h2>
          {identity.origin ? (
            <Badge variant="muted">{enumLabel(t, "cultivation:origin", identity.origin)}</Badge>
          ) : null}
          {identity.cultivation_state ? (
            <Badge variant={identity.cultivation_state === "ready" ? "success" : "info"}>
              {enumLabel(t, "cultivation:lifecycle", identity.cultivation_state)}
            </Badge>
          ) : null}
        </div>
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {identity.identity_id
            ? `${t("person:identity.identityId")}: ${identity.identity_id}`
            : t("person:identity.noIdentityId")}
        </p>
        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {t("person:identity.createdAt", {
            value: new Date(identity.created_at).toLocaleDateString(),
          })}
        </p>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
        <h3 className="text-sm font-semibold">{t("person:traitsTitle")}</h3>
        <p className="mt-1 mb-3 text-[11px] leading-4 text-muted-foreground">
          {t("person:traitsHint")}
        </p>
        <TraitsList traits={traits} />
      </section>

      <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
        <h3 className="text-sm font-semibold">{t("person:competenciesTitle")}</h3>
        <p className="mt-1 mb-3 text-[11px] leading-4 text-muted-foreground">
          {t("person:competenciesHint")}
        </p>
        <CompetencyList rows={competencies.general} />
        {!compact ? (
          <div className="mt-4">
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
        ) : null}
      </section>

      <section className="rounded-2xl border border-border bg-card p-4 shadow-card">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">{t("person:knowledgeTitle")}</h3>
          <span className="font-mono text-[11px] text-muted-foreground">
            {t("person:knowledgeTotal", { count: knowledge.total })}
          </span>
        </div>
        {knowledge.total === 0 ? (
          <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
            {t("person:knowledgeEmpty")}
          </p>
        ) : (
          <>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(knowledge.by_scope).map(([scope, count]) => (
                <Badge key={scope} variant="muted">
                  {enumLabel(t, "person:knowledgeScope", scope)} {count}
                </Badge>
              ))}
            </div>
            {knowledge.top_topics.length > 0 ? (
              <div className="mt-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("person:topicsTitle")}
                </h4>
                <ul className="mt-1 space-y-1 text-xs" data-testid="knowledge-topics">
                  {knowledge.top_topics.map((topic) => (
                    <li key={topic.topic} className="flex justify-between gap-2">
                      <span className="truncate">{topic.topic}</span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {topic.count}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </section>

      {slots?.timeline}
      {slots?.evidence}
    </div>
  );
}
