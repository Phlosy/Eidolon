import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, GitBranch, ShieldCheck, ShieldX } from "lucide-react";
import { useProjectDecisions } from "../../hooks/useProjects";
import { Badge } from "../common/badge";
import { EmptyState } from "../common/states";
import { Panel, SectionHeader } from "../shared/panel";

const STATUS_VARIANT: Record<string, "muted" | "info" | "success" | "warning" | "danger"> = {
  PROPOSED: "muted",
  EXECUTING: "info",
  APPLIED: "success",
  PARTIALLY_APPLIED: "warning",
  FAILED: "danger",
  SUPERSEDED: "muted",
};

/**
 * 项目管理决策时间线（M2.4，**只读**）。
 *
 * 展示的是三层里的第一层：**谁、为什么、结果如何**。
 * 每个动作只给定位信息（tool / outcome / authority）；完整入参出参属于执行事实，
 * 由 `/decisions/{id}/tool-audits` 提供 —— 时间线里不做审计转储。
 *
 * 刻意**不**展示任何"决策质量/打分/建议"：系统不评价管理判断（W18 / DR10）。
 */
export function DecisionTimeline({ projectId }: { projectId: number }) {
  const { t } = useTranslation();
  const query = useProjectDecisions(projectId);
  const [openId, setOpenId] = useState<number | null>(null);

  if (query.isLoading) return null;
  const decisions = query.data ?? [];

  return (
    <Panel className="p-5 md:p-6">
      <SectionHeader
        title={t("project:decisions.title")}
        description={t("project:decisions.description")}
      />
      {decisions.length === 0 ? (
        <EmptyState title={t("project:decisions.empty")} />
      ) : (
        <ul className="mt-4 space-y-3">
          {decisions.map((decision) => {
            const expanded = openId === decision.decision_id;
            return (
              <li
                key={decision.decision_id}
                className="rounded-xl border border-border bg-background/40 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={STATUS_VARIANT[decision.status] ?? "muted"}>
                    {t(`project:decisionStatus.${decision.status}`, decision.status)}
                  </Badge>
                  <span className="text-xs font-medium">
                    {t(`employee:decisionKind.${decision.decision_type}`, decision.decision_type)}
                  </span>
                  <span className="type-telemetry text-[10px] text-muted-foreground">
                    #{decision.decision_id} · {decision.acting_position_code ?? "—"} ·{" "}
                    {decision.action_summary.applied}/{decision.action_summary.total}
                  </span>
                  {decision.parent_decision_id != null ? (
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                      <GitBranch className="h-3 w-3" />#{decision.parent_decision_id}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground"
                    onClick={() => setOpenId(expanded ? null : decision.decision_id)}
                  >
                    <ChevronDown className={expanded ? "h-3 w-3 rotate-180" : "h-3 w-3"} />
                    {t("project:decisions.details")}
                  </button>
                </div>
                <p className="mt-2 text-sm leading-6">{decision.reason}</p>
                {decision.intended_outcome ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {t("project:decisions.intended")}: {decision.intended_outcome}
                  </p>
                ) : null}
                {expanded ? (
                  <div className="mt-3 space-y-2 border-t border-border/60 pt-3">
                    {decision.actions.map((action) => (
                      <div
                        key={action.audit_id}
                        className="flex flex-wrap items-center gap-2 text-[11px]"
                      >
                        {action.authority_allowed === false ? (
                          <ShieldX className="h-3.5 w-3.5 text-danger" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5 text-success" />
                        )}
                        <span className="font-medium">{action.tool_name}</span>
                        <Badge variant={action.outcome === "applied" ? "success" : "danger"}>
                          {action.outcome}
                        </Badge>
                        <span className="type-telemetry text-[10px] text-muted-foreground">
                          audit #{action.audit_id} · {action.decision_semantics}
                        </span>
                      </div>
                    ))}
                    {decision.outcome_note ? (
                      <p className="text-[11px] text-muted-foreground">{decision.outcome_note}</p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
