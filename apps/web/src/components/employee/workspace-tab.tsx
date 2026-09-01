import { useTranslation } from "react-i18next";
import { useEmployeeRuntime } from "../../hooks/useRuntimes";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import type { Employee } from "../../types";

function PathRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-xs" title={value}>
        {value}
      </dd>
    </div>
  );
}

/**
 * Workspace tab: read-only view of the runtime's workspace and data paths.
 * Falls back to the employee record's workspace path when no instance exists.
 */
export function WorkspaceTab({ employee }: { employee: Employee }) {
  const { t } = useTranslation();
  const runtimeQuery = useEmployeeRuntime(employee.id);

  if (runtimeQuery.isLoading) {
    return <Skeleton className="h-24 w-full" />;
  }
  if (runtimeQuery.isError) {
    return <ErrorState error={runtimeQuery.error} onRetry={() => runtimeQuery.refetch()} />;
  }

  const instance = runtimeQuery.data;
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">{t("employee:workspace.note")}</p>
      <dl className="grid grid-cols-1 gap-4">
        <PathRow
          label={t("employee:workspace.workspacePath")}
          value={instance?.workspace_path ?? employee.workspace_path}
        />
        {instance ? (
          <PathRow label={t("employee:workspace.dataPath")} value={instance.data_path} />
        ) : (
          <EmptyState
            title={t("employee:workspace.noRuntimeTitle")}
            hint={t("employee:workspace.noRuntimeHint")}
          />
        )}
      </dl>
    </div>
  );
}
