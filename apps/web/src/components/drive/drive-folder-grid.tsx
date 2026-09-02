import { Folder, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { DriveNode } from "../../types";

interface DriveFolderGridProps {
  folders: DriveNode[];
  allNodes: DriveNode[];
  onSelect: (node: DriveNode) => void;
}

export function DriveFolderGrid({ folders, allNodes, onSelect }: DriveFolderGridProps) {
  const { t } = useTranslation();

  if (folders.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-5 py-8 text-center text-sm text-muted-foreground">
        {t("drive:workspace.noFolders")}
      </div>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {folders.map((folder) => {
        const childCount = allNodes.filter((node) => node.parent_id === folder.id).length;
        return (
          <button
            key={folder.id}
            type="button"
            className="group flex min-h-24 items-center gap-4 rounded-2xl border border-border bg-background/55 p-4 text-left transition-colors hover:border-border-active hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onSelect(folder)}
          >
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-warning/10 text-warning">
              <Folder className="h-6 w-6 fill-current/20" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold">{folder.name}</span>
              <span className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <FolderOpen className="h-3 w-3" />
                {t("drive:workspace.itemCount", { count: childCount })}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
