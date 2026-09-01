import { describe, expect, it } from "vitest";
import { computeLayers, toFlowGraph } from "./graph";
import type { ProjectGraph } from "../types";

const graph: ProjectGraph = {
  nodes: [
    { id: "1", type: "order_review", label: "Review order", status: "done" },
    { id: "2", type: "research", label: "Research", status: "in_progress" },
    { id: "3", type: "development", label: "Build", status: "todo" },
    { id: "4", type: "testing", label: "Test", status: "backlog" },
  ],
  edges: [
    { source: "1", target: "2" },
    { source: "2", target: "3" },
    { source: "1", target: "3" },
    { source: "3", target: "4" },
  ],
};

describe("computeLayers", () => {
  it("assigns longest-path depths", () => {
    const layers = computeLayers(graph);
    expect(layers.get("1")).toBe(0);
    expect(layers.get("2")).toBe(1);
    expect(layers.get("3")).toBe(2);
    expect(layers.get("4")).toBe(3);
  });

  it("does not drop nodes on cycles", () => {
    const cyclic: ProjectGraph = {
      nodes: [
        { id: "a", type: null, label: "A", status: "todo" },
        { id: "b", type: null, label: "B", status: "todo" },
      ],
      edges: [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
      ],
    };
    const layers = computeLayers(cyclic);
    expect(layers.has("a")).toBe(true);
    expect(layers.has("b")).toBe(true);
  });
});

describe("toFlowGraph", () => {
  it("transforms the API graph into positioned flow nodes and edges", () => {
    const flow = toFlowGraph(graph, { layerGap: 200, rowGap: 100 });
    expect(flow.nodes).toHaveLength(4);
    expect(flow.edges).toHaveLength(4);

    const byId = new Map(flow.nodes.map((n) => [n.id, n]));
    expect(byId.get("1")!.position).toEqual({ x: 0, y: 0 });
    expect(byId.get("3")!.position.x).toBe(400);
    expect(byId.get("4")!.position.x).toBe(600);

    expect(byId.get("2")!.data).toMatchObject({
      label: "Research",
      status: "in_progress",
      kind: "research",
    });
    expect(flow.edges[0]).toMatchObject({ source: "1", target: "2" });
  });
});
