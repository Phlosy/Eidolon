import { useTranslation } from "react-i18next";
import { Folder } from "lucide-react";
import { DocTypeBadge } from "./doc-type-badge";
import { EmptyState } from "../common/states";
import { formatRelativeTime } from "../../utils/format";
import { DOC_TYPE_FALLBACK_META, DOC_TYPE_META } from "./constants";
import type { DriveNode, Employee } from "../../types";

interface DriveNodeListProps {
  nodes: DriveNode[];
  employees: Employee[];
  onSelectNode: (node: DriveNode) => void;
  view?: "list" | "grid";
}

function ownerName(employees: Employee[], id: number | null): string {
  if (id == null) return "—";
  return employees.find((e) => e.id === id)?.name ?? `#${id}`;
}

/** Right-panel contents table for a selected folder or zone root. */
export function DriveNodeList({ nodes, employees, onSelectNode, view = "list" }: DriveNodeListProps) {
  const { t } = useTranslation();

  if (nodes.length === 0) {
    return <EmptyState title={t("drive:emptyFolderTitle")} hint={t("drive:emptyFolderHint")} />;
  }

  if (view === "grid") {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {nodes.map((node) => {
          const meta = node.doc_type ? (DOC_TYPE_META[node.doc_type] ?? DOC_TYPE_FALLBACK_META) : DOC_TYPE_FALLBACK_META;
          const Icon = node.kind === "folder" ? Folder : meta.icon;
          return (
            <button
              key={node.id}
              type="button"
              className="group min-h-36 rounded-2xl border border-border bg-background/55 p-4 text-left transition-colors hover:border-border-active hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => onSelectNode(node)}
            >
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/8 text-primary"><Icon className="h-5 w-5" /></span>
              <span className="mt-5 block truncate text-sm font-semibold">{node.name}</span>
              <span className="mt-1 block truncate text-[11px] text-muted-foreground">{ownerName(employees, node.owner_employee_id)} · {formatRelativeTime(node.updated_at)}</span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-border bg-background/55">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/25 text-left text-xs text-muted-foreground">
            <th className="px-4 py-3 font-medium">{t("drive:columns.name")}</th>
            <th className="hidden px-4 py-3 font-medium md:table-cell">{t("drive:columns.type")}</th>
            <th className="hidden px-4 py-3 font-medium lg:table-cell">{t("drive:columns.owner")}</th>
            <th className="hidden px-4 py-3 font-medium sm:table-cell">{t("drive:columns.version")}</th>
            <th className="px-4 py-3 font-medium">{t("drive:columns.updatedAt")}</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => (
            <tr
              key={node.id}
              className="border-b border-border/60 transition-colors last:border-0 hover:bg-muted/35"
            >
              <td className="px-4 py-3">
                <button type="button" className="flex min-h-8 w-full items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onSelectNode(node)}>
                  {node.kind === "folder" ? (
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warning/10 text-warning"><Folder className="h-4 w-4 fill-current/20" /></span>
                  ) : null}
                  <span className="truncate font-medium">{node.name}</span>
                </button>
              </td>
              <td className="hidden px-4 py-3 md:table-cell">
                {node.kind === "folder" ? (
                  <span className="text-xs text-muted-foreground">{t("drive:folder")}</span>
                ) : (
                  <DocTypeBadge docType={node.doc_type} />
                )}
              </td>
              <td className="hidden px-4 py-3 text-xs text-muted-foreground lg:table-cell">
                {ownerName(employees, node.owner_employee_id)}
              </td>
              <td className="hidden px-4 py-3 font-mono text-[11px] text-muted-foreground sm:table-cell">
                {node.kind === "document" ? `v${node.current_version}` : "—"}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                {formatRelativeTime(node.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
