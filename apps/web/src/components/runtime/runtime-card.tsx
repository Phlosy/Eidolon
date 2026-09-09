import { useTranslation } from "react-i18next";
import { Button } from "../common/button";
import { RuntimeStatusBadge } from "./runtime-status-badge";
import { RuntimeUpdateBadge } from "./runtime-update-badge";
import { enumLabel } from "../../utils/labels";
import { formatDateTime } from "../../utils/format";
import type { RuntimeImageInfo, RuntimeInstance } from "../../types";

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 truncate text-sm ${mono ? "font-mono text-xs" : ""}`} title={value}>
        {value}
      </dd>
    </div>
  );
}

interface RuntimeCardProps {
  instance: RuntimeInstance;
  image?: RuntimeImageInfo | null;
  busy?: boolean;
  checkingUpdates?: boolean;
  onStop: () => void;
  onRestart: () => void;
  onViewLogs: () => void;
  onChangeProvider: () => void;
  onChangeRuntime: () => void;
  onCheckUpdate: () => void;
}

/** Runtime instance overview for the employee detail Runtime tab. */
export function RuntimeCard({
  instance,
  image,
  busy,
  checkingUpdates,
  onStop,
  onRestart,
  onViewLogs,
  onChangeProvider,
  onChangeRuntime,
  onCheckUpdate,
}: RuntimeCardProps) {
  const { t } = useTranslation();
  const active = instance.status === "running" || instance.status === "idle";
  const providerModel = instance.provider_name
    ? `${instance.provider_name} · ${instance.model ?? "—"}`
    : "—";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">
          {t("runtime:card.title", { type: enumLabel(t, "runtime:type", instance.runtime_type) })}
        </h3>
        <RuntimeStatusBadge status={instance.status} />
        {image ? (
          <RuntimeUpdateBadge
            updateAvailable={image.update_available}
            compatibilityStatus={image.compatibility_status}
          />
        ) : null}
      </div>

      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label={t("runtime:card.version")} value={instance.runtime_version ?? "—"} mono />
        <Field label={t("runtime:card.latestVersion")} value={image?.latest_version ?? "—"} mono />
        <Field label={t("runtime:card.providerModel")} value={providerModel} />
        <Field label={t("runtime:card.container")} value={instance.container_name ?? "—"} mono />
        <Field label={t("runtime:card.health")} value={instance.health_status || "—"} />
        <Field
          label={t("runtime:card.lastHealthcheck")}
          value={formatDateTime(instance.last_healthcheck_at)}
          mono
        />
        <Field label={t("runtime:card.workspace")} value={instance.workspace_path} mono />
        <Field label={t("runtime:card.dataPath")} value={instance.data_path} mono />
        <Field
          label={t("runtime:card.resources")}
          value={t("runtime:card.resourcesValue", {
            cpu: instance.cpu_limit,
            memory: instance.memory_limit_mb,
          })}
          mono
        />
      </dl>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy || !active} onClick={onStop}>
          {t("runtime:card.stop")}
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={onRestart}>
          {t("runtime:card.restart")}
        </Button>
        <Button variant="outline" size="sm" onClick={onViewLogs}>
          {t("runtime:card.viewLogs")}
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={onChangeProvider}>
          {t("runtime:card.changeProvider")}
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={onChangeRuntime}>
          {t("runtime:card.changeRuntime")}
        </Button>
        <Button variant="outline" size="sm" disabled={checkingUpdates} onClick={onCheckUpdate}>
          {checkingUpdates ? t("runtime:card.checking") : t("runtime:card.checkUpdate")}
        </Button>
      </div>
    </div>
  );
}
