import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Gauge } from "lucide-react";
import type { DriveNode, Employee, RuntimeInstance } from "../../types";
import { cn } from "../../utils/cn";
import { Panel } from "../shared/panel";
import {
  AlertCenter,
  AssetProduction,
  CompanyPulse,
  InfrastructureStatus,
} from "./operations-panels";

/**
 * 系统状态抽屉：默认收起，只留一行关键数字。
 *
 * 首页的重心是公司和队伍，运行细节（脉搏/基础设施/资产/告警）不该同时铺开。
 * 有告警时自动展开，平时需要看再点开。
 */
export function SystemStatusDrawer({
  employees,
  runtimes,
  nodes,
}: {
  employees: Employee[];
  runtimes: RuntimeInstance[];
  nodes: DriveNode[];
}) {
  const { t } = useTranslation();
  const incidents = runtimes.filter((runtime) =>
    ["unhealthy", "crashed", "error"].includes(runtime.status),
  ).length;
  const employeeErrors = employees.filter((employee) => employee.status === "error").length;
  const alertCount = incidents + employeeErrors;
  const health =
    runtimes.length === 0
      ? 100
      : Math.max(0, Math.round(((runtimes.length - incidents) / runtimes.length) * 100));
  const assets = nodes.filter((node) => node.kind === "document").length;
  const [open, setOpen] = useState(alertCount > 0);
  useEffect(() => {
    if (alertCount > 0) setOpen(true);
  }, [alertCount]);

  const chip =
    "flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background/50 px-2.5";
  return (
    <Panel className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center justify-between gap-3 p-4 text-left transition-colors hover:bg-surface-interactive/40 md:p-5"
      >
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary/20 bg-primary/8 text-primary">
            <Gauge className="h-4 w-4" />
          </span>
          <div>
            <p className="type-overline text-muted-foreground">{t("dashboard:status.kicker")}</p>
            <p className="type-h4 mt-0.5">{t("dashboard:status.title")}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={chip} title={t("dashboard:status.health")}>
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            <span className="type-telemetry text-[11px]">{health}%</span>
          </span>
          <span className={chip} title={t("dashboard:status.alerts")}>
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                alertCount > 0 ? "bg-warning" : "bg-success",
              )}
            />
            <span
              className={cn(
                "type-telemetry text-[11px]",
                alertCount > 0 && "font-semibold text-warning",
              )}
            >
              {alertCount}
            </span>
          </span>
          <span className={chip} title={t("dashboard:status.assets")}>
            <span className="h-1.5 w-1.5 rounded-full bg-primary" />
            <span className="type-telemetry text-[11px]">{assets}</span>
          </span>
          <span className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground">
            <ChevronDown
              className={cn("h-4 w-4 transition-transform", open && "rotate-180")}
              aria-label={open ? t("dashboard:status.collapse") : t("dashboard:status.expand")}
            />
          </span>
        </div>
      </button>
      {open ? (
        <div className="grid gap-4 border-t border-border p-4 md:p-5 xl:grid-cols-2">
          <CompanyPulse />
          <InfrastructureStatus runtimes={runtimes} />
          <AssetProduction nodes={nodes} />
          <AlertCenter employees={employees} runtimes={runtimes} />
        </div>
      ) : null}
    </Panel>
  );
}
