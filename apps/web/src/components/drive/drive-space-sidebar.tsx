import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock3, File, Files, Folder, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { buildDriveTree, type DriveTreeNode } from "../../utils/drive-tree";
import { DOC_TYPE_FALLBACK_META, DOC_TYPE_META, DRIVE_ZONES, ZONE_META } from "./constants";
import type { DriveNode, DriveZone } from "../../types";

export type DriveLocation = "all" | "recent" | DriveZone;

interface DriveSpaceSidebarProps {
  active: DriveLocation | null;
  nodes: DriveNode[];
  selectedNodeId: number | null;
  onOpenLocation: (location: DriveLocation) => void;
  onOpenNode: (node: DriveNode) => void;
}

export function DriveSpaceSidebar({
  active,
  nodes,
  selectedNodeId,
  onOpenLocation,
  onOpenNode,
}: DriveSpaceSidebarProps) {
  const { t } = useTranslation();
  const [expandedZones, setExpandedZones] = useState<Set<DriveZone>>(new Set());
  const [expandedFolders, setExpandedFolders] = useState<Set<number>>(new Set());
  const trees = useMemo(() => {
    const result = new Map<DriveZone, DriveTreeNode[]>();
    for (const zone of DRIVE_ZONES) {
      const zoneTree = buildDriveTree(nodes.filter((node) => node.zone === zone));
      const explicitRoot = zoneTree.find((entry) => entry.node.path === `drive/${zone}`);
      result.set(zone, explicitRoot ? explicitRoot.children : zoneTree);
    }
    return result;
  }, [nodes]);

  const toggleZone = (zone: DriveZone) =>
    setExpandedZones((current) => {
      const next = new Set(current);
      if (next.has(zone)) next.delete(zone);
      else next.add(zone);
      return next;
    });

  const toggleFolder = (id: number) =>
    setExpandedFolders((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const locationClass = (selected: boolean) =>
    cn(
      "flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm transition-colors active:bg-muted",
      selected
        ? "bg-primary/10 font-medium text-primary"
        : "text-muted-foreground hover:bg-muted hover:text-foreground",
    );

  const renderTree = (tree: DriveTreeNode[], depth = 0): React.ReactNode =>
    tree.map(({ node, children }) => {
      const isFolder = node.kind === "folder";
      const isOpen = isFolder && expandedFolders.has(node.id);
      const meta = node.doc_type
        ? (DOC_TYPE_META[node.doc_type] ?? DOC_TYPE_FALLBACK_META)
        : DOC_TYPE_FALLBACK_META;
      const NodeIcon = isFolder ? (isOpen ? FolderOpen : Folder) : (meta.icon ?? File);

      return (
        <div key={node.id} role="treeitem" aria-expanded={isFolder ? isOpen : undefined}>
          <div
            className={cn(
              "group flex min-h-10 items-center rounded-lg transition-colors",
              selectedNodeId === node.id
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            style={{ paddingLeft: `${depth * 14 + 6}px` }}
          >
            {isFolder ? (
              <button
                type="button"
                className="flex h-9 w-7 shrink-0 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={isOpen ? t("drive:collapse") : t("drive:expand")}
                tabIndex={-1}
                onClick={() => toggleFolder(node.id)}
              >
                {isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="w-7 shrink-0" />
            )}
            <button
              type="button"
              data-testid={`drive-tree-node-${node.id}`}
              className="flex min-h-10 min-w-0 flex-1 items-center gap-2 rounded-md pr-2 text-left text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title={isFolder ? t("drive:workspace.folderInteractionHint") : node.name}
              onClick={(event) => {
                if (isFolder) {
                  if (event.detail === 1) toggleFolder(node.id);
                } else {
                  onOpenNode(node);
                }
              }}
              onDoubleClick={() => {
                if (isFolder) onOpenNode(node);
              }}
              onKeyDown={(event) => {
                if (!isFolder) return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  onOpenNode(node);
                }
                if (event.key === " ") {
                  event.preventDefault();
                  toggleFolder(node.id);
                }
              }}
            >
              <NodeIcon
                className={cn("h-4 w-4 shrink-0", isFolder ? "text-warning" : "text-primary")}
              />
              <span className="truncate">{node.name}</span>
            </button>
          </div>
          {isFolder && isOpen && children.length > 0 ? (
            <div role="group">{renderTree(children, depth + 1)}</div>
          ) : null}
        </div>
      );
    });

  return (
    <aside className="border-b border-border bg-muted/20 p-4 lg:min-h-[720px] lg:border-b-0 lg:border-r">
      <p className="px-3 pb-2 type-kicker text-muted-foreground">
        {t("drive:workspace.navigation")}
      </p>
      <nav
        className="grid grid-cols-2 gap-1 lg:block lg:space-y-1"
        aria-label={t("drive:workspace.navigation")}
      >
        <button
          type="button"
          className={locationClass(active === "all")}
          onClick={() => onOpenLocation("all")}
        >
          <Files className="h-[18px] w-[18px]" />
          <span className="min-w-0 flex-1 truncate">{t("drive:workspace.allFiles")}</span>
          <span className="type-telemetry text-[10px]">{nodes.length}</span>
        </button>
        <button
          type="button"
          className={locationClass(active === "recent")}
          onClick={() => onOpenLocation("recent")}
        >
          <Clock3 className="h-[18px] w-[18px]" />
          <span className="min-w-0 flex-1 truncate">{t("drive:workspace.recent")}</span>
        </button>
      </nav>

      <div className="mt-4 border-t border-border pt-4">
        <p className="px-3 pb-2 type-kicker text-muted-foreground">{t("drive:workspace.spaces")}</p>
        <div className="space-y-1" role="tree" aria-label={t("drive:workspace.spaces")}>
          {DRIVE_ZONES.map((zone) => {
            const meta = ZONE_META[zone];
            const Icon = meta.icon;
            const tree = trees.get(zone) ?? [];
            const open = expandedZones.has(zone);
            const count = nodes.filter(
              (node) => node.zone === zone && node.kind === "document",
            ).length;
            return (
              <div key={zone} role="treeitem" aria-expanded={open}>
                <div
                  className={cn(
                    "flex min-h-11 items-center rounded-xl",
                    active === zone
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <button
                    type="button"
                    tabIndex={-1}
                    className="flex h-10 w-8 shrink-0 items-center justify-center rounded-lg"
                    aria-label={open ? t("drive:collapse") : t("drive:expand")}
                    onClick={() => toggleZone(zone)}
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </button>
                  <button
                    type="button"
                    data-testid={`drive-zone-${zone}`}
                    className="flex min-h-11 min-w-0 flex-1 items-center gap-2.5 rounded-lg pr-3 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    title={t("drive:workspace.folderInteractionHint")}
                    onClick={(event) => {
                      if (event.detail === 1) toggleZone(zone);
                    }}
                    onDoubleClick={() => onOpenLocation(zone)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        onOpenLocation(zone);
                      }
                      if (event.key === " ") {
                        event.preventDefault();
                        toggleZone(zone);
                      }
                    }}
                  >
                    <span
                      className={cn(
                        "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border",
                        meta.tileClass,
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1 truncate">{t(`drive:zone.${zone}`)}</span>
                    <span className="type-telemetry text-[10px]">{count}</span>
                  </button>
                </div>
                {open && tree.length > 0 ? <div role="group">{renderTree(tree)}</div> : null}
              </div>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
