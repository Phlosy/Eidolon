import { describe, expect, it } from "vitest";
import { buildDriveTree, sortDriveNodes } from "./drive-tree";
import type { DriveNode } from "../types";

let nextId = 1;

function makeNode(overrides: Partial<DriveNode> = {}): DriveNode {
  return {
    id: nextId++,
    parent_id: null,
    kind: "document",
    name: "node",
    path: "node",
    zone: "projects",
    project_id: null,
    doc_type: null,
    owner_employee_id: null,
    current_version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("buildDriveTree", () => {
  it("nests children under their parents regardless of flat-list order", () => {
    const root = makeNode({ kind: "folder", name: "project" });
    const docs = makeNode({ kind: "folder", name: "docs", parent_id: root.id });
    const prd = makeNode({ name: "prd.md", parent_id: docs.id });
    const src = makeNode({ kind: "folder", name: "source", parent_id: root.id });

    // Deliberately scrambled input order.
    const tree = buildDriveTree([prd, src, docs, root]);

    expect(tree).toHaveLength(1);
    expect(tree[0].node.id).toBe(root.id);
    expect(tree[0].children.map((c) => c.node.name)).toEqual(["docs", "source"]);
    expect(tree[0].children[0].children.map((c) => c.node.name)).toEqual(["prd.md"]);
  });

  it("orders folders first, then alphabetically (case-insensitive)", () => {
    const b = makeNode({ name: "beta.md" });
    const folder = makeNode({ kind: "folder", name: "Zulu" });
    const a = makeNode({ name: "Alpha.md" });

    const tree = buildDriveTree([b, folder, a]);
    expect(tree.map((t) => t.node.name)).toEqual(["Zulu", "Alpha.md", "beta.md"]);
  });

  it("treats nodes with a missing parent as roots (pre-filtered lists)", () => {
    const orphan = makeNode({ name: "orphan.md", parent_id: 999 });
    const root = makeNode({ kind: "folder", name: "root" });

    const tree = buildDriveTree([orphan, root]);
    expect(tree).toHaveLength(2);
    expect(tree[0].node.name).toBe("root");
    expect(tree[1].node.name).toBe("orphan.md");
  });
});

describe("sortDriveNodes", () => {
  it("sorts a flat sibling list without mutating the input", () => {
    const b = makeNode({ name: "b.md" });
    const folder = makeNode({ kind: "folder", name: "a" });
    const input = [b, folder];

    const sorted = sortDriveNodes(input);
    expect(sorted.map((n) => n.name)).toEqual(["a", "b.md"]);
    expect(input.map((n) => n.name)).toEqual(["b.md", "a"]);
  });
});
