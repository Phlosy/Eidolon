import { useTranslation } from "react-i18next";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import type { EducationEvent } from "../../api/cultivation";

const KIND_VARIANT: Record<string, "default" | "info" | "violet" | "warning" | "muted"> = {
  course: "info",
  exam: "violet",
  project: "default",
  internship: "default",
  competition: "warning",
  fortune: "warning",
};

function traitLabel(t: ReturnType<typeof useTranslation>["t"], code: string): string {
  const key = `cultivation:traits.${code}`;
  const translated = t(key);
  return translated === key ? code : translated;
}

/**
 * 阶段标题：模板里的阶段名是后端注册表的中文数据，属结构性标签 ——
 * 有 stage_id 时走 i18n（中英一致），拿不到才回落到事件 topic。
 */
function eventTitle(t: ReturnType<typeof useTranslation>["t"], event: EducationEvent): string {
  const stageId = event.outcome.stage_id;
  if (stageId) {
    const key = `cultivation:stage.${stageId}`;
    const translated = t(key);
    if (translated !== key) return translated;
  }
  return event.topic;
}

function FortuneBody({ event }: { event: EducationEvent }) {
  const { t } = useTranslation();
  const outcome = event.outcome;
  const delta = outcome.signal_delta ?? 0;
  const shifts = Object.entries(outcome.trait_shift ?? {});
  return (
    <div className="space-y-1.5">
      {outcome.narrative ? <p className="text-xs text-foreground">{outcome.narrative}</p> : null}
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {delta !== 0 ? (
          <span className={delta > 0 ? "text-success" : "text-danger"}>
            {delta > 0
              ? t("cultivation:timeline.signalBoost", { value: delta })
              : t("cultivation:timeline.signalDrop", { value: delta })}
          </span>
        ) : null}
        {outcome.extra_topics?.length ? (
          <span>
            {t("cultivation:timeline.extraTopics")}: {outcome.extra_topics.join("、")}
          </span>
        ) : null}
        {shifts.length ? (
          <span>
            {t("cultivation:timeline.traitShift")}:{" "}
            {shifts
              .map(([code, value]) => `${traitLabel(t, code)} ${value >= 0 ? "+" : ""}${value}`)
              .join("、")}
          </span>
        ) : null}
      </p>
    </div>
  );
}

function StageBody({ event }: { event: EducationEvent }) {
  const { t } = useTranslation();
  const outcome = event.outcome;
  const topics = outcome.topics ?? [];
  const signals = outcome.signals ?? [];
  return (
    <div className="space-y-1.5">
      {topics.length ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">
            {t("cultivation:timeline.topics")}:
          </span>
          {topics.map((topic, index) => (
            <Badge key={`${topic}-${index}`} variant="muted" className="font-normal">
              {topic}
              {signals[index] != null ? (
                <span className="ml-1 font-mono text-[10px] text-muted-foreground">
                  {signals[index]}
                </span>
              ) : null}
            </Badge>
          ))}
        </div>
      ) : null}
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {outcome.knowledge_produced != null ? (
          <span>
            {t("cultivation:timeline.knowledgeProduced", { count: outcome.knowledge_produced })}
          </span>
        ) : null}
        {outcome.duration_weeks != null ? (
          <span>{t("cultivation:timeline.durationWeeks", { count: outcome.duration_weeks })}</span>
        ) : null}
        {outcome.signal != null ? (
          <span>{t("cultivation:timeline.signal", { value: outcome.signal })}</span>
        ) : null}
        {outcome.assessment_run_id != null ? (
          <span className="text-info">{t("cultivation:timeline.assessed")}</span>
        ) : null}
      </p>
    </div>
  );
}

/**
 * 履历时间线（新→旧）：履历即证据链，每条事件都能反查到当次学习的产出。
 * 际遇事件（fortune）以叙事 + 偏移量展示，读起来像一段经历而不是一条日志。
 */
export function EducationTimeline({ events }: { events: EducationEvent[] }) {
  const { t } = useTranslation();
  if (events.length === 0) {
    return (
      <section
        className="rounded-2xl border border-border bg-card p-4 shadow-card"
        data-testid="education-timeline"
      >
        <h3 className="text-sm font-semibold">{t("cultivation:timeline.title")}</h3>
        <p className="mt-2 text-xs text-muted-foreground">{t("cultivation:timeline.empty")}</p>
      </section>
    );
  }
  const ordered = [...events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
  return (
    <section
      className="rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="education-timeline"
    >
      <h3 className="text-sm font-semibold">{t("cultivation:timeline.title")}</h3>
      <ol className="mt-3 space-y-3">
        {ordered.map((event) => (
          <li key={event.id} className="relative border-l border-border pl-4">
            <span
              className={
                event.kind === "fortune"
                  ? "absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-warning"
                  : "absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-primary"
              }
            />
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={KIND_VARIANT[event.kind] ?? "muted"}>
                {enumLabel(t, "cultivation:kind", event.kind)}
              </Badge>
              <span className="text-xs font-medium">{eventTitle(t, event)}</span>
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                {new Date(event.occurred_at).toLocaleDateString()}
              </span>
            </div>
            <div className="mt-1.5">
              {event.kind === "fortune" ? (
                <FortuneBody event={event} />
              ) : (
                <StageBody event={event} />
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
