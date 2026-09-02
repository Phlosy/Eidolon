import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ProjectGanttChart } from "./project-gantt-chart";
import type { Employee, ProjectDetail } from "../../types";

const project: ProjectDetail = {
  id: 7,
  company_id: 1,
  name: "Atlas launch",
  description: "Launch plan",
  status: "in_progress",
  goal: "Ship Atlas",
  owner_id: 2,
  source_order_text: "Ship Atlas",
  planned_start_at: "2026-09-01T00:00:00Z",
  planned_end_at: "2026-09-20T00:00:00Z",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
  milestones: [
    {
      id: 70,
      project_id: 7,
      name: "Build",
      description: "Build stage",
      status: "in_progress",
      order: 1,
      owner_id: 3,
      planned_start_at: "2026-09-04T00:00:00Z",
      planned_end_at: "2026-09-12T00:00:00Z",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    },
  ],
  tasks: [
    {
      id: 700,
      project_id: 7,
      milestone_id: 70,
      title: "Implement API",
      description: null,
      kind: "development",
      status: "in_progress",
      priority: 5,
      assignee_id: 3,
      acceptance_criteria: null,
      sequence: 1,
      dependencies: [],
      planned_start_at: "2026-09-04T00:00:00Z",
      planned_end_at: "2026-09-12T00:00:00Z",
      actual_start_at: "2026-09-05T00:00:00Z",
      actual_end_at: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    },
  ],
  artifacts: [],
};

const employees = [
  {
    id: 3,
    company_id: 1,
    department_id: 1,
    name: "Morgan Chen",
    slug: "morgan",
    role: "engineer",
    title: "Engineer",
    avatar: null,
    status: "working",
    lifecycle_status: "active",
    username: "morgan",
    runtime_type: "mock",
    runtime_config: {},
    workspace_path: "",
    memory_namespace: "",
    current_task_id: 700,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
  },
] satisfies Employee[];

describe("ProjectGanttChart", () => {
  it("shows the complete project, milestone, task, and owner hierarchy", () => {
    render(<ProjectGanttChart projects={[project]} employees={employees} />, {
      wrapper: MemoryRouter,
    });
    expect(screen.getByText("Atlas launch")).toBeInTheDocument();
    expect(screen.getByText("Build")).toBeInTheDocument();
    expect(screen.getByText("Implement API")).toBeInTheDocument();
    expect(screen.getAllByTitle("Morgan Chen").length).toBeGreaterThan(0);
  });

  it("lets portfolio owners expand milestone detail on demand", () => {
    render(<ProjectGanttChart projects={[project]} employees={employees} mode="portfolio" />, {
      wrapper: MemoryRouter,
    });
    expect(screen.queryByText("Implement API")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /展开下级任务|Expand child work/i }));
    expect(screen.getByText("Implement API")).toBeInTheDocument();
  });

  it("coarsens multi-year portfolios instead of rendering one DOM column per day", () => {
    const longProject = {
      ...project,
      planned_end_at: "2028-09-01T00:00:00Z",
      milestones: [],
      tasks: [],
    };
    render(<ProjectGanttChart projects={[longProject]} employees={employees} mode="portfolio" />, {
      wrapper: MemoryRouter,
    });
    expect(screen.getAllByTestId("gantt-axis-column").length).toBeLessThan(40);
  });
});
