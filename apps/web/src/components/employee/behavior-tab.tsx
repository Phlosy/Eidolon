import { useTranslation } from "react-i18next";
import {
  useEmployeeBrain,
  useEmployeeBrainProjection,
  useEmployeeSkillUsageBenchmarks,
  useUpdateEmployeeBrain,
} from "../../hooks/useEmployees";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";

/** 把策略摘要渲染成"它会怎么工作"的中文额度表。数值全部来自后端。 */
function QuotaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}

export function BehaviorTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const brainQuery = useEmployeeBrain(employeeId);
  const projectionQuery = useEmployeeBrainProjection(employeeId);
  const benchmarksQuery = useEmployeeSkillUsageBenchmarks(employeeId);
  // 写入走 traits（权威），后端同步 legacy 镜像列；越界/未知 trait 由服务端 422 拦住。
  const updateBrain = useUpdateEmployeeBrain(employeeId);

  if (brainQuery.isLoading) return <Skeleton className="h-40 w-full" />;
  const brain = brainQuery.data;
  const policy = brain?.behavior;
  if (!brain || !policy) {
    return <p className="text-sm text-destructive">{t("employee:behavior.unavailable")}</p>;
  }

  const percent = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}%`);

  return (
    <div className="space-y-4" data-tutorial-target="employee-behavior-tab">
      <section className="rounded-md border border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium">{t("employee:behavior.title")}</h3>
          <Badge variant="muted">
            {t(`employee:behavior.band.${policy.band}`)} · {policy.policy_version}
          </Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{t("employee:behavior.hint")}</p>

        <label className="mt-4 block">
          <span className="flex items-center justify-between text-xs font-medium">
            {t("employee:behavior.curiosity")}
            <span className="font-mono text-muted-foreground">
              {policy.traits.curiosity.toFixed(2)}
            </span>
          </span>
          <input
            className="mt-3 w-full accent-foreground"
            type="range"
            min="0"
            max="1"
            step="0.1"
            defaultValue={policy.traits.curiosity}
            onPointerUp={(event) =>
              updateBrain.mutate({ traits: { curiosity: Number(event.currentTarget.value) } })
            }
          />
        </label>
        {updateBrain.isError ? (
          <p className="mt-2 text-xs text-red-600 dark:text-red-400">{updateBrain.error.message}</p>
        ) : null}

        <div className="mt-4 grid gap-x-8 gap-y-0 sm:grid-cols-2">
          <QuotaRow
            label={t("employee:behavior.quota.knowledgeLimit")}
            value={String(policy.retrieval.knowledge_limit)}
          />
          <QuotaRow
            label={t("employee:behavior.quota.maxContext")}
            value={String(policy.retrieval.max_context_items)}
          />
          <QuotaRow
            label={t("employee:behavior.quota.candidateSkills")}
            value={
              policy.retrieval.include_candidate_skills
                ? t("employee:behavior.quota.on")
                : t("employee:behavior.quota.off")
            }
          />
          <QuotaRow
            label={t("employee:behavior.quota.openQuestions")}
            value={String(policy.reflection.open_question_count)}
          />
          <QuotaRow
            label={t("employee:behavior.quota.hypotheses")}
            value={String(policy.reflection.alternative_hypotheses)}
          />
          <QuotaRow
            label={t("employee:behavior.quota.followupTopics")}
            value={String(policy.learning.followup_topics_per_task)}
          />
        </div>

        {policy.work_directives.length > 0 ? (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            {policy.work_directives.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-xs text-muted-foreground">
            {t("employee:behavior.noDirectives")}
          </p>
        )}
      </section>

      <section className="rounded-md border border-border p-4">
        <h3 className="text-sm font-medium">{t("employee:behavior.benchmarkTitle")}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("employee:behavior.benchmarkHint")}</p>
        <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <div className="text-xs text-muted-foreground">
              {t("employee:behavior.metric.trial")}
            </div>
            <div className="font-mono">{percent(benchmarksQuery.data?.trial_rate ?? null)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">
              {t("employee:behavior.metric.conversion")}
            </div>
            <div className="font-mono">
              {percent(benchmarksQuery.data?.conversion_rate ?? null)}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">
              {t("employee:behavior.metric.useful")}
            </div>
            <div className="font-mono">{percent(benchmarksQuery.data?.useful_rate ?? null)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">
              {t("employee:behavior.metric.pending")}
            </div>
            <div className="font-mono">{benchmarksQuery.data?.pending_ratings ?? 0}</div>
          </div>
        </div>
      </section>

      {projectionQuery.data ? (
        <section className="rounded-md border border-border p-4">
          <h3 className="text-sm font-medium">{t("employee:behavior.projectionTitle")}</h3>
          <dl className="mt-2 space-y-1 text-xs">
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t("employee:behavior.projectionRevision")}</dt>
              <dd className="font-mono">{projectionQuery.data.revision}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t("employee:behavior.projectionFiles")}</dt>
              <dd className="truncate font-mono" title={projectionQuery.data.paths.join("\n")}>
                {projectionQuery.data.paths.length}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{t("employee:behavior.projectionMirror")}</dt>
              <dd className="font-mono">
                {projectionQuery.data.mirror_current
                  ? t("employee:behavior.projectionMirrorOn")
                  : t("employee:behavior.projectionMirrorOff")}
              </dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">
            {t("employee:behavior.projectionNote")}
          </p>
        </section>
      ) : null}
    </div>
  );
}
