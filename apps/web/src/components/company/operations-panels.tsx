import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowRight,
  Box,
  Cpu,
  FileCode2,
  FileText,
  RadioTower,
  Server,
  ShieldCheck,
} from "lucide-react";
import type { DriveNode, Employee, RuntimeInstance } from "../../types";
import { ActivityFeed } from "../../features/activity-feed/activity-feed";
import { Panel, SectionHeader } from "../shared/panel";

export function CompanyPulse() {
  const { t } = useTranslation();
  return (
    <Panel className="p-5 md:p-6">
      <SectionHeader
        kicker={t("dashboard:pulse.kicker")}
        title={t("dashboard:pulse.title")}
        description={t("dashboard:pulse.description")}
        icon={RadioTower}
      />
      <ActivityFeed limit={12} />
    </Panel>
  );
}

export function InfrastructureStatus({ runtimes }: { runtimes: RuntimeInstance[] }) {
  const { t } = useTranslation();
  const running = runtimes.filter(
    (runtime) => runtime.status === "running" || runtime.status === "idle",
  ).length;
  const incidents = runtimes.filter((runtime) =>
    ["unhealthy", "crashed", "error"].includes(runtime.status),
  ).length;
  const health =
    runtimes.length === 0
      ? 100
      : Math.max(0, Math.round(((runtimes.length - incidents) / runtimes.length) * 100));
  return (
    <Panel className="p-5">
      <SectionHeader
        kicker={t("dashboard:infrastructure.kicker")}
        title={t("dashboard:infrastructure.title")}
        icon={Cpu}
        action={
          <Link to="/runtime" className="text-primary">
            <ArrowRight className="h-4 w-4" />
          </Link>
        }
      />
      <div className="flex items-end justify-between">
        <div>
          <p className="type-telemetry text-4xl font-semibold">{health}%</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("dashboard:infrastructure.health")}
          </p>
        </div>
        <span
          className={
            incidents
              ? "flex h-11 w-11 items-center justify-center rounded-xl bg-danger/10 text-danger"
              : "flex h-11 w-11 items-center justify-center rounded-xl bg-success/10 text-success"
          }
        >
          {incidents ? <AlertTriangle className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
        </span>
      </div>
      <div className="mt-5 grid grid-cols-2 gap-2">
        <div className="rounded-xl border border-border bg-background/45 p-3">
          <Server className="h-3.5 w-3.5 text-primary" />
          <p className="type-telemetry mt-3 text-lg font-semibold">
            {running}/{runtimes.length}
          </p>
          <p className="text-[10px] text-muted-foreground">{t("dashboard:infrastructure.nodes")}</p>
        </div>
        <div className="rounded-xl border border-border bg-background/45 p-3">
          <AlertTriangle className="h-3.5 w-3.5 text-warning" />
          <p className="type-telemetry mt-3 text-lg font-semibold">{incidents}</p>
          <p className="text-[10px] text-muted-foreground">
            {t("dashboard:infrastructure.incidents")}
          </p>
        </div>
      </div>
    </Panel>
  );
}

export function AssetProduction({ nodes }: { nodes: DriveNode[] }) {
  const { t } = useTranslation();
  const documents = nodes.filter((node) => node.kind === "document");
  const projectDocs = documents.filter((node) => node.zone === "projects").length;
  const knowledgeDocs = documents.filter((node) => node.zone === "knowledge").length;
  return (
    <Panel className="p-5">
      <SectionHeader
        kicker={t("dashboard:assets.kicker")}
        title={t("dashboard:assets.title")}
        icon={Box}
        action={
          <Link to="/drive" className="text-primary">
            <ArrowRight className="h-4 w-4" />
          </Link>
        }
      />
      <p className="type-telemetry text-4xl font-semibold">{documents.length}</p>
      <p className="mt-1 text-xs text-muted-foreground">{t("dashboard:assets.total")}</p>
      <div className="mt-5 space-y-2">
        <div className="flex items-center justify-between rounded-xl border border-border bg-background/45 px-3 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <FileCode2 className="h-3.5 w-3.5 text-primary" />
            {t("dashboard:assets.project")}
          </span>
          <span className="type-telemetry">{projectDocs}</span>
        </div>
        <div className="flex items-center justify-between rounded-xl border border-border bg-background/45 px-3 py-2.5 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <FileText className="h-3.5 w-3.5 text-secondary" />
            {t("dashboard:assets.knowledge")}
          </span>
          <span className="type-telemetry">{knowledgeDocs}</span>
        </div>
      </div>
    </Panel>
  );
}

export function AlertCenter({
  employees,
  runtimes,
}: {
  employees: Employee[];
  runtimes: RuntimeInstance[];
}) {
  const { t } = useTranslation();
  const employeeErrors = employees.filter((employee) => employee.status === "error").length;
  const offline = employees.filter((employee) => employee.status === "offline").length;
  const runtimeErrors = runtimes.filter((runtime) =>
    ["unhealthy", "crashed", "error"].includes(runtime.status),
  ).length;
  const alerts = [
    [employeeErrors, t("dashboard:alerts.employeeErrors")],
    [runtimeErrors, t("dashboard:alerts.runtimeErrors")],
    [offline, t("dashboard:alerts.offline")],
  ] as const;
  return (
    <Panel className="p-5">
      <SectionHeader
        kicker={t("dashboard:alerts.kicker")}
        title={t("dashboard:alerts.title")}
        icon={AlertTriangle}
      />
      <div className="space-y-2">
        {alerts.map(([count, label]) => (
          <div
            key={label}
            className="flex items-center justify-between rounded-xl border border-border bg-background/45 px-3 py-2.5"
          >
            <span className="text-xs text-muted-foreground">{label}</span>
            <span
              className={
                count > 0
                  ? "type-telemetry text-sm font-semibold text-warning"
                  : "type-telemetry text-sm font-semibold text-success"
              }
            >
              {count}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[10px] leading-4 text-muted-foreground">
        {employeeErrors + runtimeErrors === 0
          ? t("dashboard:alerts.clear")
          : t("dashboard:alerts.review")}
      </p>
    </Panel>
  );
}
