import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DriveDocumentViewer } from "./drive-document-viewer";

vi.mock("../../hooks/useDrive", () => ({
  useDriveNode: () => ({
    data: {
      id: 42,
      parent_id: 1,
      kind: "document",
      name: "Launch.md",
      path: "drive/knowledge/launch.md",
      zone: "knowledge",
      project_id: null,
      doc_type: "note",
      owner_employee_id: null,
      current_version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      content: "# Launch plan\n\n**Ready** for review.",
      collaborators: [],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDriveNodeContent: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useDriveRevisions: () => ({ data: [], isLoading: false }),
  useUpdateDriveNode: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

describe("DriveDocumentViewer", () => {
  it("renders Markdown and offers native, DOCX, and PDF exports", () => {
    render(<DriveDocumentViewer nodeId={42} employees={[]} />);

    expect(screen.getByRole("heading", { name: "Launch plan" })).toBeInTheDocument();
    expect(screen.getByText("Ready").tagName).toBe("STRONG");
    expect(screen.getByRole("option", { name: "Markdown (.md)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Word document (.docx)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "PDF document (.pdf)" })).toBeInTheDocument();
  });
});
