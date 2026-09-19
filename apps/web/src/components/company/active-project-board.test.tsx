import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Project } from "../../types";
import { ActiveProjectBoard } from "./active-project-board";

function project(id: number, status: Project["status"]): Project {
  return { id, name: `Project ${id}`, status, owner_id: null } as Project;
}

describe("ActiveProjectBoard summaries", () => {
  it("preserves visible order, status filtering, limit, task progress and blockers", () => {
    render(
      <ActiveProjectBoard
        projects={[
          project(8, "completed"),
          project(7, "waiting_for_management"),
          project(6, "cancelled"),
          project(5, "in_review"),
          project(4, "rejected"),
          project(3, "in_progress"),
          project(2, "planning"),
          project(1, "requested"),
        ]}
        summaries={[
          { project_id: 7, tasks_total: 0, tasks_completed: 0, blockers: 0 },
          { project_id: 5, tasks_total: 9, tasks_completed: 1, blockers: 2 },
          { project_id: 3, tasks_total: 3, tasks_completed: 2, blockers: 1 },
          { project_id: 2, tasks_total: 4, tasks_completed: 2, blockers: 0 },
        ]}
        employees={[]}
      />,
      { wrapper: MemoryRouter },
    );
    const links = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href") !== "/projects");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/projects/7",
      "/projects/5",
      "/projects/3",
      "/projects/2",
    ]);
    expect(within(links[0]).getByText("0%")).toBeInTheDocument();
    expect(within(links[0]).getByText("0/0 tasks complete")).toBeInTheDocument();
    expect(within(links[1]).getByText("11%")).toBeInTheDocument();
    expect(within(links[1]).getByText("1/9 tasks complete")).toBeInTheDocument();
    expect(within(links[1]).getByText("2 blockers")).toBeInTheDocument();
    expect(within(links[2]).getByText("67%")).toBeInTheDocument();
    expect(within(links[2]).getByText("1 blocker")).toBeInTheDocument();
  });

  it("keeps the empty project board state", () => {
    render(<ActiveProjectBoard projects={[]} summaries={[]} employees={[]} />, {
      wrapper: MemoryRouter,
    });
    expect(
      screen.getByText("No active projects. Create the company's next mission."),
    ).toBeInTheDocument();
  });
});
