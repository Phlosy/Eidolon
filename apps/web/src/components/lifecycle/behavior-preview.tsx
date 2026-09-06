import { useTranslation } from "react-i18next";
import { useBehaviorPreview } from "../../hooks/useBehavior";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";

/**
 * 招聘向导里的人格预览：把滑杆数字翻译成"它会怎么工作"。
 *
 * 档位与额度全部由 `GET /behavior/preview` 返回 —— 阈值只有后端一份，
 * 前端不再自己写 `curiosity < 0.4` 这类判断（设计契约 §3.3）。
 */
export function BehaviorPreview({
  curiosity,
  learningEnabled,
}: {
  curiosity: number;
  learningEnabled: boolean;
}) {
  const { t } = useTranslation();
  const preview = useBehaviorPreview("curiosity", curiosity, learningEnabled);

  if (preview.isError) {
    return (
      <p className="mt-2 text-xs text-red-600 dark:text-red-400">{t("common:errorFallback")}</p>
    );
  }
  const policy = preview.data;
  if (!policy) return <Skeleton className="mt-3 h-16 w-full" />;

  const items: Array<[string, string]> = [
    [t("lifecycle:wizard.behaviorPreview.knowledge"), String(policy.retrieval.knowledge_limit)],
    [
      t("lifecycle:wizard.behaviorPreview.candidateSkills"),
      policy.retrieval.include_candidate_skills
        ? t("lifecycle:wizard.behaviorPreview.on")
        : t("lifecycle:wizard.behaviorPreview.off"),
    ],
    [
      t("lifecycle:wizard.behaviorPreview.openQuestions"),
      String(policy.reflection.open_question_count),
    ],
    [
      t("lifecycle:wizard.behaviorPreview.followups"),
      String(policy.learning.followup_topics_per_task),
    ],
  ];

  return (
    <div className="mt-3 rounded-md border border-border/60 bg-muted/30 p-3" data-behavior-preview>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{t("lifecycle:wizard.behaviorPreview.title")}</span>
        <Badge variant="muted">{t(`lifecycle:wizard.behaviorPreview.band.${policy.band}`)}</Badge>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
        {items.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-mono text-xs">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        {learningEnabled
          ? t("lifecycle:wizard.behaviorPreview.note")
          : t("lifecycle:wizard.behaviorPreview.noteLearningOff")}
      </p>
    </div>
  );
}
