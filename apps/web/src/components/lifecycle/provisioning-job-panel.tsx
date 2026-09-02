import { useTranslation } from "react-i18next";
import { RotateCcw } from "lucide-react";
import { useProvisioningJob, useRetryProvisioningJob } from "../../hooks/useLifecycle";
import { Badge } from "../common/badge";
import { Button } from "../common/button";
import { Skeleton } from "../common/skeleton";
import { StepStatusIcon } from "./step-status-icon";
import { PROVISIONING_JOB_STATUS_VARIANT } from "../../utils/status";
import { enumLabel } from "../../utils/labels";
import { cn } from "../../utils/cn";

/**
 * Live view of a provisioning job: ordered steps with status icons and error
 * text, progress bar, and a Retry button while failed steps exist. The hook
 * polls GET /provisioning-jobs/{id} every 2s while the job is running.
 */
export function ProvisioningJobPanel({ jobId }: { jobId: number }) {
  const { t } = useTranslation();
  const jobQuery = useProvisioningJob(jobId);
  const retry = useRetryProvisioningJob();

  if (jobQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (jobQuery.isError || !jobQuery.data) {
    return (
      <p className="text-xs text-red-600 dark:text-red-400">
        {jobQuery.error instanceof Error ? jobQuery.error.message : t("common:errorFallback")}
      </p>
    );
  }
  const job = jobQuery.data;
  const steps = [...(job.steps ?? [])].sort((a, b) => a.seq - b.seq);
  const hasFailed = steps.some((s) => s.status === "failed");
  const showRetry = hasFailed || job.status === "failed" || job.status === "partial";
  const progress = job.total_steps > 0 ? (job.done_steps / job.total_steps) * 100 : 0;

  return (
    <div
      data-testid="provisioning-job-panel"
      className="rounded-lg border border-border bg-card p-4 shadow-card"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">
            {t("lifecycle:job.title")} · {enumLabel(t, "lifecycle:job.kind", job.kind)}
          </p>
          <Badge variant={PROVISIONING_JOB_STATUS_VARIANT[job.status]}>
            {enumLabel(t, "lifecycle:job.status", job.status)}
          </Badge>
        </div>
        <span className="font-mono text-xs text-muted-foreground">
          {t("lifecycle:job.progress", { done: job.done_steps, total: job.total_steps })}
        </span>
      </div>

      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          data-testid="job-progress-bar"
          className={cn(
            "h-full rounded-full transition-all",
            job.status === "failed" ? "bg-status-error" : "bg-status-working",
          )}
          style={{ width: `${progress}%` }}
        />
      </div>

      {steps.length > 0 ? (
        <ul className="space-y-1.5">
          {steps.map((step) => (
            <li key={step.id} data-testid="job-step" className="flex items-start gap-2.5 text-sm">
              <span className="mt-0.5">
                <StepStatusIcon status={step.status} />
              </span>
              <div className="min-w-0 flex-1">
                <p>
                  {step.description ?? step.action}
                  <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                    {step.provider_key}
                  </span>
                </p>
                {step.status === "failed" && step.error ? (
                  <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">{step.error}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {showRetry ? (
        <div className="mt-3 flex justify-end">
          <Button
            variant="outline"
            size="sm"
            data-testid="job-retry"
            disabled={retry.isPending}
            onClick={() => retry.mutate(job.id)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {retry.isPending ? t("lifecycle:job.retrying") : t("lifecycle:job.retry")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
