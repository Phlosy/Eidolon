import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Handshake, ShieldAlert } from "lucide-react";
import { useProjectSpec } from "../../hooks/useProjects";
import { Badge } from "../common/badge";
import { Panel } from "../shared/panel";

/**
 * 项目的工作模式与「谁负责接收」面板（M2.1）。
 *
 * 三件必须让玩家看见的事：
 *
 * 1. 这个项目是 **guided（引导/协助）** 还是 **managed（自主管理）**、
 *    或是不属于任何一类的历史项目；
 * 2. 两者的差别只有**人类参与程度**，不是"谁负责决策"；
 * 3. 如果连负责接收的人都没有（`waiting_for_management`），
 *    明确告诉玩家**去哪儿修** —— 系统不会替公司规划，也不会随便挑人。
 */
export function ProjectWorkModePanel({ projectId, status }: { projectId: number; status: string }) {
  const { t } = useTranslation();
  const spec = useProjectSpec(projectId).data;
  if (!spec) return null;

  const mode = spec.work_mode;
  const modeLabel =
    mode === "guided"
      ? t("project:workMode.guided")
      : mode === "managed"
        ? t("project:workMode.managed")
        : t("project:workMode.legacy");

  const waiting = status === "waiting_for_management" || !spec.work_intake.assignment;

  return (
    <Panel className="p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="type-kicker text-primary">{t("project:workMode.title")}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={mode === "managed" ? "violet" : "info"}>{modeLabel}</Badge>
            {spec.planning_fixture === "deterministic_template" ? (
              <Badge variant="muted">deterministic_template</Badge>
            ) : null}
            <span className="text-[10px] text-muted-foreground">
              {t("project:workMode.intakePosition", {
                code: spec.work_intake.position_code || "—",
              })}
            </span>
          </div>
        </div>
        <Link
          to="/positions"
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent"
        >
          {t("project:workMode.managePositions")}
        </Link>
      </div>
      <p className="mt-3 max-w-3xl text-xs leading-5 text-muted-foreground">
        {t("project:workMode.hint")}
      </p>

      {waiting ? (
        <div
          className="mt-4 rounded-xl border border-warning/40 bg-warning/10 p-4"
          data-testid="project-waiting-for-management"
        >
          <p className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlert className="h-4 w-4 text-warning" />
            {t("project:waitingForManagement.title")}
          </p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {t("project:waitingForManagement.hint")}
          </p>
          {spec.work_intake.reason ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {t("project:waitingForManagement.reason")}
              {"："}
              {spec.work_intake.reason}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
          <Handshake className="h-3.5 w-3.5 text-primary" />
          {t("project:workMode.managedBy", {
            id: spec.management.employee_id ?? "—",
            position: spec.management.position_code ?? "—",
          })}
          {spec.management.stale ? ` · ${t("project:workMode.stale")}` : ""}
        </p>
      )}
    </Panel>
  );
}
