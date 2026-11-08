import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { renderPage } from "../../test/render";
import { ProjectPage } from "./project-page";

const deliver = vi.fn();
const project = {
  project_id: "p1",
  company_id: "c1",
  mission_id: "m1",
  title: "Notes CLI",
  budget: 20_000,
  manager: "pm-1",
  status: "active",
  plan_decision_id: "d0",
  created_at: "2026-11-07T09:00:00Z",
  updated_at: "2026-11-07T10:00:00Z",
  spend_micro: 123_000,
  tasks: [
    {
      task_id: "t1",
      project_id: "p1",
      title: "Storage module",
      description: "JSON file storage",
      acceptance_criteria: ["adds", "lists"],
      kind: "work",
      role: "engineer",
      status: "done",
      assignee: "eng-1",
      depends_on: [],
      inputs: [],
      rework_count: 1,
      current_run_id: "r1",
      reviews: [{ reviewer: "qa-1", verdict: "pass", reason: "ok" }],
      created_at: "2026-11-07T09:10:00Z",
      updated_at: "2026-11-07T09:50:00Z",
    },
  ],
  artifacts: [
    {
      artifact_id: "a1",
      project_id: "p1",
      task_id: "t1",
      run_id: "r1",
      path: "notes.py",
      blob_ref: "sha256:aa",
      size: 1200,
      media_type: "text/x-python",
      uses: [],
      created_at: "2026-11-07T09:40:00Z",
    },
  ],
};

vi.mock("../../features/auth/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../features/auth/auth-context")>();
  return {
    ...actual,
    useAuth: () => ({
      me: {
        user_id: "u1",
        email: "o@x.io",
        display_name: "Owner",
        person_ids: ["owner-1"],
        companies: [{ company_id: "c1", person_id: "owner-1", role: "owner", title: "Owner" }],
        session_id: "s",
      },
      isLoading: false,
      error: null,
      setMe: vi.fn(),
      refresh: vi.fn(),
    }),
  };
});
vi.mock("../../api", () => ({
  companies: {
    one: vi.fn(() => Promise.resolve({ company_id: "c1", name: "Tiny Studio", currency: "CNY" })),
  },
  work: {
    project: vi.fn(() => Promise.resolve(project)),
    deliver: (...a: unknown[]) => deliver(...a),
    report: vi.fn(() =>
      Promise.resolve({
        kind: "project",
        id: "p1",
        company_id: "c1",
        title: "Notes CLI",
        status: "active",
        lang: "en",
        as_of_fact_id: "f9",
        sections: [
          {
            key: "done",
            title: "What was done",
            lines: [
              {
                text: "Task “Storage module” done by eng-1, review passed by qa-1",
                facts: ["f1234567-0000-0000-0000-000000000000"],
                link: { kind: "task", id: "t1" },
              },
            ],
          },
        ],
        needs_you: [],
      }),
    ),
  },
  provenance: { trace: vi.fn(() => Promise.resolve({ fact: {}, edges: [], facts: [] })) },
  ledger: {
    projectCosts: vi.fn(() =>
      Promise.resolve({
        project_id: "p1",
        currency: "CNY",
        total_micro: 123_000,
        task_micro: 100_000,
        management_micro: 23_000,
        tasks: [
          { task_id: "t1", title: "Storage module", status: "done", micro: 100_000, runs: [] },
        ],
        management: [{ run_id: "r9", person_id: "tl-1", micro: 23_000 }],
      }),
    ),
  },
}));

describe("ProjectPage", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await i18n.changeLanguage("en-US");
  });

  it("renders tasks with reviews, artifacts and costs, and lets the owner accept delivery", async () => {
    renderPage(<ProjectPage />, { path: "/projects/:id", route: "/projects/p1" });
    expect(await screen.findByRole("heading", { name: "Notes CLI" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Storage module")[0]);
    expect(screen.getByText("qa-1")).toBeInTheDocument();
    expect(screen.getByText("notes.py")).toBeInTheDocument();
    expect((await screen.findAllByText("0.023 CNY")).length).toBeGreaterThan(0); // management runs
    deliver.mockResolvedValue({ status: "approved", applied: "project.delivered" });
    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept delivery" }));
    await waitFor(() => expect(deliver).toHaveBeenCalledWith("p1", "ship it"));
  });

  it("tells the story on the report tab, each line with its facts", async () => {
    renderPage(<ProjectPage />, { path: "/projects/:id", route: "/projects/p1" });
    await screen.findByRole("heading", { name: "Notes CLI" });
    fireEvent.click(screen.getByRole("tab", { name: "Report" }));
    expect(
      await screen.findByText("Task “Storage module” done by eng-1, review passed by qa-1"),
    ).toBeInTheDocument();
    expect(screen.getByText("What was done")).toBeInTheDocument();
    expect(screen.getByText(/1 facts/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /trace f1234567/ })).toBeInTheDocument();
  });
});
