import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, History } from "lucide-react";
import { formatDateTime } from "../../utils/format";
import type { DriveRevision, Employee } from "../../types";

interface DriveRevisionsProps {
  revisions: DriveRevision[];
  employees: Employee[];
  isLoading?: boolean;
}

function authorName(employees: Employee[], id: number | null): string {
  if (id == null) return "—";
  return employees.find((e) => e.id === id)?.name ?? `#${id}`;
}

/** Collapsible revision history for a document (newest first, as returned by the API). */
export function DriveRevisions({ revisions, employees, isLoading }: DriveRevisionsProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors hover:bg-muted/50"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <History className="h-3.5 w-3.5 text-muted-foreground" />
        {t("drive:revisions.title")}
        <span className="font-mono text-[11px] text-muted-foreground">{revisions.length}</span>
      </button>
      {open ? (
        <div className="border-t border-border px-4 py-2">
          {isLoading ? (
            <p className="py-2 text-xs text-muted-foreground">…</p>
          ) : revisions.length === 0 ? (
            <p className="py-2 text-xs text-muted-foreground">{t("drive:revisions.empty")}</p>
          ) : (
            <ul className="divide-y divide-border/60">
              {revisions.map((rev) => (
                <li key={rev.version} className="flex flex-wrap items-baseline gap-x-3 py-2">
                  <span className="font-mono text-xs font-medium">v{rev.version}</span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {rev.sha256.slice(0, 8)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {authorName(employees, rev.author_employee_id)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs">
                    {rev.message ?? <span className="text-muted-foreground">—</span>}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {formatDateTime(rev.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
