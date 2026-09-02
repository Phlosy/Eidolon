import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DriveSpaceSidebar } from "./drive-space-sidebar";
import type { DriveNode } from "../../types";

const base = {
  zone: "projects" as const,
  project_id: 1,
  doc_type: null,
  owner_employee_id: null,
  current_version: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const nodes: DriveNode[] = [
  {
    ...base,
    id: 1,
    parent_id: null,
    kind: "folder",
    name: "Portal",
    path: "drive/projects/portal",
  },
  {
    ...base,
    id: 2,
    parent_id: 1,
    kind: "folder",
    name: "Specs",
    path: "drive/projects/portal/specs",
  },
  {
    ...base,
    id: 3,
    parent_id: 2,
    kind: "document",
    name: "PRD.md",
    path: "drive/projects/portal/specs/prd.md",
    doc_type: "prd",
    current_version: 1,
  },
];

describe("DriveSpaceSidebar", () => {
  it("expands folders on one click and opens them on double click", () => {
    const onOpenLocation = vi.fn();
    const onOpenNode = vi.fn();
    render(
      <DriveSpaceSidebar
        active="all"
        nodes={nodes}
        selectedNodeId={null}
        onOpenLocation={onOpenLocation}
        onOpenNode={onOpenNode}
      />,
    );

    fireEvent.click(screen.getByTestId("drive-zone-projects"), { detail: 1 });
    expect(screen.getByTestId("drive-tree-node-1")).toBeInTheDocument();
    expect(onOpenLocation).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("drive-tree-node-1"), { detail: 1 });
    expect(screen.getByTestId("drive-tree-node-2")).toBeInTheDocument();

    fireEvent.doubleClick(screen.getByTestId("drive-tree-node-1"));
    expect(onOpenNode).toHaveBeenCalledWith(nodes[0]);
  });

  it("opens a document on one click", () => {
    const onOpenNode = vi.fn();
    render(
      <DriveSpaceSidebar
        active="all"
        nodes={nodes}
        selectedNodeId={null}
        onOpenLocation={vi.fn()}
        onOpenNode={onOpenNode}
      />,
    );

    fireEvent.click(screen.getByTestId("drive-zone-projects"), { detail: 1 });
    fireEvent.click(screen.getByTestId("drive-tree-node-1"), { detail: 1 });
    fireEvent.click(screen.getByTestId("drive-tree-node-2"), { detail: 1 });
    fireEvent.click(screen.getByTestId("drive-tree-node-3"), { detail: 1 });
    expect(onOpenNode).toHaveBeenCalledWith(nodes[2]);
  });
});
