import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EmployeeCard } from "./employee-card";
import type { Employee, EmployeeStatus } from "../../types";

function makeEmployee(status: EmployeeStatus): Employee {
  return {
    id: 1,
    company_id: 1,
    department_id: 1,
    name: "Charlie Doe",
    slug: "charlie",
    role: "engineer",
    title: "Senior Engineer",
    avatar: null,
    status,
    runtime_type: "mock",
    runtime_config: {},
    workspace_path: "data/workspaces/charlie",
    memory_namespace: "emp_charlie",
    current_task_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function renderCard(status: EmployeeStatus) {
  return render(
    <MemoryRouter>
      <EmployeeCard employee={makeEmployee(status)} />
    </MemoryRouter>,
  );
}

describe("EmployeeCard", () => {
  it("renders the working status with a green pulsing dot", () => {
    renderCard("working");
    expect(screen.getByTestId("status-label")).toHaveTextContent("Working");
    const dots = screen.getAllByTestId("status-dot");
    expect(dots[0]).toHaveClass("bg-emerald-500");
    expect(dots[0]).toHaveClass("status-pulse");
  });

  it("renders the researching status in blue", () => {
    renderCard("researching");
    expect(screen.getByTestId("status-label")).toHaveTextContent("Researching");
    expect(screen.getAllByTestId("status-dot")[0]).toHaveClass("bg-blue-500");
  });

  it("renders the offline status dimmed without a pulse", () => {
    renderCard("offline");
    expect(screen.getByTestId("status-label")).toHaveTextContent("Offline");
    const dot = screen.getAllByTestId("status-dot")[0];
    expect(dot).not.toHaveClass("status-pulse");
  });

  it("renders name, title and avatar initials", () => {
    renderCard("idle");
    expect(screen.getByText("Charlie Doe")).toBeInTheDocument();
    expect(screen.getByText("Senior Engineer")).toBeInTheDocument();
    expect(screen.getByText("CD")).toBeInTheDocument();
  });

  it("shows the current task with its #EID id when working", () => {
    const employee = { ...makeEmployee("working"), current_task_id: 42 };
    render(
      <MemoryRouter>
        <EmployeeCard
          employee={employee}
          currentTask={{
            id: 42,
            project_id: 1,
            milestone_id: null,
            title: "Build the API",
            description: null,
            kind: "development",
            status: "in_progress",
            priority: 1,
            assignee_id: 1,
            acceptance_criteria: null,
            sequence: 1,
            dependencies: [],
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Build the API")).toBeInTheDocument();
    expect(screen.getByText("#EID-42")).toBeInTheDocument();
  });
});
