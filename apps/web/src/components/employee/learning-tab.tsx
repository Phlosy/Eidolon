import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useEmployeeLearning } from "../../hooks/useLearning";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";

const STATUS_VARIANT: Record<string, "success" | "info" | "warning" | "danger" | "muted"> = {
  completed: "success",
  running: "info",
  planned: "muted",
  waiting_budget: "warning",
  failed: "danger",
  cancelled: "muted",
};

/** Employee Detail → 学习 tab：政策/成本提示 + 手工学习 + 最近会话（P11）。 */
export function LearningTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const { policyQuery, sessionsQuery, startMutation, cancelMutation } =
    useEmployeeLearning(employeeId);
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState("web_research");

  if (sessionsQuery.isLoading) return <Skeleton className="h-40 w-full" />;
  const policy = policyQuery.data;
  const sessions = sessionsQuery.data ?? [];

  return (
    <div className="space-y-5" data-testid="learning-tab">
      <header>
        <h3 className="text-sm font-semibold">{t("employee:learning.learningTitle")}</h3>
        <p className="text-xs text-muted-foreground">{t("employee:learning.learningHint")}</p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={policy?.enabled ? "success" : "info"} data-testid="learning-enabled">
          {policy?.enabled
            ? t("employee:learning.statusRunning")
            : t("employee:learning.disabledHint")}
        </Badge>
        <span className="text-xs text-muted-foreground">{t("employee:learning.costWarning")}</span>
      </div>

      <section className="rounded-md border border-border p-3">
        <h4 className="mb-2 text-xs font-medium">{t("employee:learning.manualStart")}</h4>
        <div className="flex flex-wrap gap-2">
          <input
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder={t("employee:learning.topic")}
            className="min-w-[220px] flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs"
          />
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
          >
            <option value="web_research">{t("employee:learning.webResearch")}</option>
            <option value="knowledge_review">{t("employee:learning.knowledgeReview")}</option>
            <option value="document_study">{t("employee:learning.documentStudy")}</option>
          </select>
          <button
            onClick={() => {
              if (!topic.trim()) return;
              startMutation.mutate({ topic: topic.trim(), learning_mode: mode });
              setTopic("");
            }}
            className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground"
            data-testid="start-learning"
          >
            {t("employee:learning.start")}
          </button>
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-medium text-muted-foreground">
          {t("employee:learning.recentSessions")}
        </h4>
        <ul className="space-y-1.5">
          {sessions.slice(0, 15).map((session) => (
            <li
              key={session.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border px-3 py-2"
              data-testid={`learning-session-${session.id}`}
            >
              <span className="text-sm">{session.topic}</span>
              <Badge variant={STATUS_VARIANT[session.status] ?? "muted"}>{session.status}</Badge>
              <span className="font-mono text-[11px] text-muted-foreground">
                {session.learning_mode} · {session.runtime_type}
              </span>
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {t("employee:learning.budget")}: {session.tokens_used}/{session.budget_tokens}
              </span>
              {session.status === "running" || session.status === "planned" ? (
                <button
                  onClick={() => cancelMutation.mutate(session.id)}
                  className="rounded-md border border-border px-2 py-1 text-[11px] hover:bg-muted"
                >
                  {t("employee:learning.cancel")}
                </button>
              ) : null}
            </li>
          ))}
          {sessions.length === 0 ? <p className="text-xs text-muted-foreground">—</p> : null}
        </ul>
      </section>
    </div>
  );
}
