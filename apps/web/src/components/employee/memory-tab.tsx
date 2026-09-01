import { useTranslation } from "react-i18next";
import { useEmployeeMemory } from "../../hooks/useEmployees";
import { EmptyState, ErrorState } from "../common/states";
import { ScrollArea, Skeleton } from "../common/skeleton";
import { Badge } from "../common/badge";
import { enumLabel } from "../../utils/labels";
import { formatDateTime } from "../../utils/format";

/** Memory tab: the employee's memory entries (notes, observations, summaries). */
export function MemoryTab({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const memoryQuery = useEmployeeMemory(employeeId);

  if (memoryQuery.isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  if (memoryQuery.isError) {
    return <ErrorState error={memoryQuery.error} onRetry={() => memoryQuery.refetch()} />;
  }
  const entries = memoryQuery.data ?? [];
  if (entries.length === 0) {
    return (
      <EmptyState title={t("employee:memory.emptyTitle")} hint={t("employee:memory.emptyHint")} />
    );
  }
  return (
    <ScrollArea className="max-h-96 pr-3">
      <ul className="divide-y divide-border/60">
        {entries.map((entry) => (
          <li key={entry.id} className="py-2.5">
            <div className="flex items-center gap-2">
              <Badge variant="muted">{enumLabel(t, "employee:memory.kind", entry.kind)}</Badge>
              <span className="font-mono text-[11px] text-muted-foreground">
                {formatDateTime(entry.created_at)}
              </span>
            </div>
            <p className="mt-1 text-sm whitespace-pre-wrap">{entry.content}</p>
          </li>
        ))}
      </ul>
    </ScrollArea>
  );
}
