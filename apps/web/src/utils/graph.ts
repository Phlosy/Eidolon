import type { Edge, Node } from "@xyflow/react";
import type { ProjectGraph, ProjectGraphNode } from "../types";

export interface GraphLayoutOptions {
  /** Horizontal distance between layers. */
  layerGap?: number;
  /** Vertical distance between nodes within a layer. */
  rowGap?: number;
}

export interface FlowNodeData extends Record<string, unknown> {
  label: string;
  status: ProjectGraphNode["status"];
  kind: string | null;
}

export interface FlowGraph {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
}

/**
 * Layered layout for the project workflow graph (no dagre):
 * layer = longest-path depth from a root (topological), nodes within a
 * layer stack vertically in input order.
 */
export function computeLayers(graph: ProjectGraph): Map<string, number> {
  const ids = graph.nodes.map((n) => n.id);
  const depth = new Map<string, number>(ids.map((id) => [id, 0]));

  // Kahn's algorithm; depth[v] = max(depth[u] + 1) over edges u -> v.
  const indegree = new Map<string, number>(ids.map((id) => [id, 0]));
  const outgoing = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!depth.has(edge.source) || !depth.has(edge.target)) continue;
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    const list = outgoing.get(edge.source) ?? [];
    list.push(edge.target);
    outgoing.set(edge.source, list);
  }

  const queue = ids.filter((id) => (indegree.get(id) ?? 0) === 0);
  const visited = new Set<string>();
  while (queue.length > 0) {
    const id = queue.shift()!;
    visited.add(id);
    for (const next of outgoing.get(id) ?? []) {
      depth.set(next, Math.max(depth.get(next)!, depth.get(id)! + 1));
      indegree.set(next, indegree.get(next)! - 1);
      if (indegree.get(next) === 0) queue.push(next);
    }
  }

  // Cycle guard: leave unvisited nodes at their current depth so the
  // layout still renders instead of dropping nodes.
  return depth;
}

export function toFlowGraph(graph: ProjectGraph, options: GraphLayoutOptions = {}): FlowGraph {
  const { layerGap = 260, rowGap = 96 } = options;
  const layers = computeLayers(graph);
  const rowCounters = new Map<number, number>();

  const nodes: Node<FlowNodeData>[] = graph.nodes.map((node) => {
    const layer = layers.get(node.id) ?? 0;
    const row = rowCounters.get(layer) ?? 0;
    rowCounters.set(layer, row + 1);
    return {
      id: node.id,
      type: "task",
      position: { x: layer * layerGap, y: row * rowGap },
      data: { label: node.label, status: node.status, kind: node.type },
    };
  });

  const edges: Edge[] = graph.edges.map((edge, index) => ({
    id: `e-${edge.source}-${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
  }));

  return { nodes, edges };
}
