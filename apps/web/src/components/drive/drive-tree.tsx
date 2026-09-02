import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronRight,
  File,
  Folder,
  FolderOpen,
} from "lucide-react";
import { cn } from "../../utils/cn";
import { buildDriveTree, type DriveTreeNode } from "../../utils/drive-tree";
import {
  DOC_TYPE_FALLBACK_META,
  DOC_TYPE_META,
  DRIVE_ZONES,
  ZONE_META,
} from "./constants";
import type { DriveNode, DriveZone } from "../../types";

interface DriveTreeProps {
  nodes: DriveNode[];
  selectedNodeId: number | null;
  onSelectNode: (node: DriveNode) => void;
  /** When true (default), nodes are grouped under the four zone roots. */
  showZones?: boolean;
  selectedZone?: DriveZone | null;
  onSelectZone?: (zone: DriveZone) => void;
}

/**
 * Left-panel tree. Zones render as fixed roots; folders expand/collapse and are
 * selectable (their contents show in the right panel); documents select for preview.
 */
export function DriveTree({
  nodes,
  selectedNodeId,
  onSelectNode,
  showZones = true,
  selectedZone = null,
  onSelectZone,
}: DriveTreeProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());

  const toggle = (id: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });

  const renderNodes = (tree: DriveTreeNode[], depth: number): React.ReactNode =>
    tree.map(({ node, children }) => {
      const isFolder = node.kind === "folder";
      const isCollapsed = collapsed.has(node.id);
      const isSelected = selectedNodeId === node.id;
      const DocIcon = node.doc_type
        ? (DOC_TYPE_META[node.doc_type]?.icon ?? DOC_TYPE_FALLBACK_META.icon)
        : null;
      return (
        <div key={node.id}>
          <div
            className={cn(
              "group flex w-full items-center gap-1 rounded-md text-left text-sm transition-colors",
              isSelected ? "bg-accent/10 font-medium text-foreground" : "hover:bg-muted",
            )}
            style={{ paddingLeft: `${depth * 14 + 4}px` }}
          >
            {isFolder ? (
              <button
                type="button"
                aria-label={isCollapsed ? t("drive:expand") : t("drive:collapse")}
                aria-expanded={!isCollapsed}
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                onClick={() => toggle(node.id)}
              >
                {isCollapsed ? (
                  <ChevronRight className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="w-[22px] shrink-0" />
            )}
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-1.5 truncate px-1 py-1.5"
              onClick={() => onSelectNode(node)}
            >
              {isFolder ? (
                isCollapsed ? (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )
              ) : DocIcon ? (
                <DocIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <File className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate">{node.name}</span>
              {!isFolder && node.current_version > 0 ? (
                <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
                  v{node.current_version}
                </span>
              ) : null}
            </button>
          </div>
          {isFolder && !isCollapsed ? renderNodes(children, depth + 1) : null}
        </div>
      );
    });

  if (!showZones) {
    return <div className="space-y-0.5">{renderNodes(buildDriveTree(nodes), 0)}</div>;
  }

  return (
    <div className="space-y-2">
      {DRIVE_ZONES.map((zone) => {
        const meta = ZONE_META[zone];
        const ZoneIcon = meta.icon;
        const zoneNodes = nodes.filter((n) => n.zone === zone);
        const zoneTree = buildDriveTree(zoneNodes);
        const zoneOpen = !collapsed.has(-DRIVE_ZONES.indexOf(zone) - 1);
        const zoneKey = -DRIVE_ZONES.indexOf(zone) - 1;
        return (
          <div key={zone}>
            <div
              className={cn(
                "flex w-full items-center gap-1 rounded-md transition-colors",
                selectedZone === zone ? "bg-accent/10" : "hover:bg-muted",
              )}
            >
              <button
                type="button"
                aria-label={zoneOpen ? t("drive:collapse") : t("drive:expand")}
                aria-expanded={zoneOpen}
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                onClick={() => toggle(zoneKey)}
              >
                {zoneOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
              <button
                type="button"
                data-testid={`drive-zone-${zone}`}
                className="flex min-w-0 flex-1 items-center gap-2 px-1 py-1.5"
                onClick={() => onSelectZone?.(zone)}
              >
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded border",
                    meta.tileClass,
                  )}
                >
                  <ZoneIcon className="h-3 w-3" />
                </span>
                <span className="truncate text-sm font-medium">{t(`drive:zone.${zone}`)}</span>
                <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                  {zoneNodes.filter((n) => n.kind === "document").length}
                </span>
              </button>
            </div>
            {zoneOpen && zoneTree.length > 0 ? (
              <div className="mt-0.5">{renderNodes(zoneTree, 1)}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
