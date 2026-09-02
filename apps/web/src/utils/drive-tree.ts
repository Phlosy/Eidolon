import type { DriveNode } from "../types";

export interface DriveTreeNode {
  node: DriveNode;
  children: DriveTreeNode[];
}

/** Folders first, then alphabetical by name (case-insensitive, locale-aware). */
function compareNodes(a: DriveNode, b: DriveNode): number {
  if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
  return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
}

/** Flat sibling ordering (folders first, then name) for the contents table. */
export function sortDriveNodes(nodes: DriveNode[]): DriveNode[] {
  return [...nodes].sort(compareNodes);
}

/**
 * Build a nested tree from the flat GET /drive/tree payload. Roots are nodes
 * whose parent_id is null OR whose parent is missing from the list (e.g. when
 * the list is pre-filtered by zone or project).
 */
export function buildDriveTree(nodes: DriveNode[]): DriveTreeNode[] {
  const byParent = new Map<number, DriveNode[]>();
  const ids = new Set(nodes.map((n) => n.id));
  const roots: DriveNode[] = [];

  for (const node of nodes) {
    if (node.parent_id == null || !ids.has(node.parent_id)) {
      roots.push(node);
    } else {
      const siblings = byParent.get(node.parent_id) ?? [];
      siblings.push(node);
      byParent.set(node.parent_id, siblings);
    }
  }

  const build = (node: DriveNode): DriveTreeNode => ({
    node,
    children: (byParent.get(node.id) ?? []).sort(compareNodes).map(build),
  });

  return roots.sort(compareNodes).map(build);
}
