import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DrivePage } from "./drive-page";
import type { DriveNode } from "../../types";

const NODES: DriveNode[] = [
  {
    id: 1,
    parent_id: null,
    kind: "folder",
    name: "portal-redesign",
    path: "projects/portal-redesign",
    zone: "projects",
    project_id: 1,
    doc_type: null,
    owner_employee_id: null,
    current_version: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    parent_id: 1,
    kind: "document",
    name: "prd.md",
    path: "projects/portal-redesign/docs/prd.md",
    zone: "projects",
    project_id: 1,
    doc_type: "prd",
    owner_employee_id: 1,
    current_version: 3,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
  {
    id: 3,
    parent_id: null,
    kind: "document",
    name: "onboarding.md",
    path: "handbook/onboarding.md",
    zone: "handbook",
    project_id: null,
    doc_type: "handbook",
    owner_employee_id: null,
    current_version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 4,
    parent_id: 1,
    kind: "document",
    name: "legacy-source.py",
    path: "projects/portal-redesign/legacy-source.py",
    zone: "projects",
    project_id: 1,
    doc_type: "source_code" as DriveNode["doc_type"],
    owner_employee_id: 1,
    current_version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

vi.mock("../../hooks/useDrive", () => ({
  useDriveTree: () => ({ data: NODES, isLoading: false, isError: false, error: null }),
  useDriveNode: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useDriveRevisions: () => ({ data: [], isLoading: false }),
  useUpdateDriveNode: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useCreateDriveFolder: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useUploadDriveFile: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useCreateDriveDocument: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../../hooks/useEmployees", () => ({
  useEmployees: () => ({ data: [], isLoading: false, isError: false, error: null }),
}));

describe("DrivePage", () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={["/drive"]}>
        <DrivePage />
      </MemoryRouter>,
    );

  it("faishu-style icon dropdowns: create/upload unfold their own entries", () => {
    renderPage();
    const createTrigger = screen.getByRole("button", { name: "Create" });
    fireEvent.click(createTrigger);
    expect(screen.getByTestId("drive-create-menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "New document" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "New folder" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "New table" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByTestId("drive-upload-menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Upload file" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Upload folder" })).toBeInTheDocument();
  });

  it("no big 'Drive' title header at top — toolbar starts right away", () => {
    renderPage();
    expect(screen.queryByRole("heading", { name: "Drive" })).not.toBeInTheDocument();
  });

  it("renders the four document spaces in the workspace navigation", () => {
    renderPage();

    expect(screen.getByTestId("drive-zone-projects")).toHaveTextContent("Projects");
    expect(screen.getByTestId("drive-zone-knowledge")).toHaveTextContent("Knowledge");
    expect(screen.getByTestId("drive-zone-skills")).toHaveTextContent("Skills");
    expect(screen.getByTestId("drive-zone-handbook")).toHaveTextContent("Handbook");
  });

  it("shows folders and documents in the cloud workspace", () => {
    renderPage();

    expect(screen.getByText("portal-redesign")).toBeInTheDocument();
    expect(screen.getByText("prd.md")).toBeInTheDocument();
    expect(screen.getByText("onboarding.md")).toBeInTheDocument();
    expect(screen.getByText("legacy-source.py")).toBeInTheDocument();
  });

  it("opens on the all-files workspace instead of an empty tree selection", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "All files" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Folders" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing selected")).not.toBeInTheDocument();
  });
});
