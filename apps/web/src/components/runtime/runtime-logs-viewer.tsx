import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useRuntimeLogs } from "../../hooks/useRuntimes";
import { Button } from "../common/button";
import { ErrorState } from "../common/states";
import { ScrollArea, Skeleton } from "../common/skeleton";

interface RuntimeLogsViewerProps {
  runtimeId: number;
  tail?: number;
}

/**
 * Runtime logs: tail fetch with manual refresh, plus an optional 3s poll
 * toggle. Log lines are rendered as-is (the backend redacts secrets) inside
 * a pre, so no markup from log content is interpreted.
 */
export function RuntimeLogsViewer({ runtimeId, tail = 200 }: RuntimeLogsViewerProps) {
  const { t } = useTranslation();
  const [polling, setPolling] = useState(false);
  const logsQuery = useRuntimeLogs(runtimeId, tail, true, polling ? 3000 : undefined);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {t("runtime:logs.lastLines", { count: tail })}
        </p>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={polling}
              onChange={(e) => setPolling(e.target.checked)}
            />
            {t("runtime:logs.live")}
          </label>
          <Button
            variant="outline"
            size="sm"
            disabled={logsQuery.isFetching}
            onClick={() => logsQuery.refetch()}
          >
            <RefreshCw
              className={logsQuery.isFetching ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
            />
            {t("runtime:logs.refresh")}
          </Button>
        </div>
      </div>
      {logsQuery.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : logsQuery.isError ? (
        <ErrorState error={logsQuery.error} onRetry={() => logsQuery.refetch()} />
      ) : (
        <ScrollArea className="h-72 rounded-md border border-border bg-muted/40">
          <pre className="p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all">
            {(logsQuery.data?.lines ?? []).join("\n") || t("runtime:logs.empty")}
          </pre>
        </ScrollArea>
      )}
    </div>
  );
}
