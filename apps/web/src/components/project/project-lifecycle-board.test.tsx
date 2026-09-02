import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { ProjectLifecycle } from "../../types";
import { ProjectLifecycleBoard } from "./project-lifecycle-board";

const lifecycle = {
  project: {
    id: 9,
    company_id: 1,
    name: "Classic Snake",
    code: "SNAKE",
    description: "",
    status: "in_review",
    goal: "",
    owner_id: 1,
    source_order_text: "",
    priority: "high",
    customer: "Tutorial",
    background: "",
    objectives: [],
    technical_requirements: [],
    constraints: [],
    deliverables: [],
    review_configuration: {},
    participants: {},
    tutorial_accelerated: true,
    created_at: "2026-09-02T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
  },
  phases: [
    {
      id: 1,
      project_id: 9,
      phase_type: "initiation",
      name: "Project Initiation",
      order: 0,
      status: "completed",
      started_at: null,
      completed_at: null,
      gate_required: false,
      review_id: null,
      baseline_id: null,
      owner_employee_id: 1,
      metadata_json: {},
      created_at: "2026-09-02T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    },
    {
      id: 2,
      project_id: 9,
      phase_type: "requirements_review",
      name: "Requirements Review",
      order: 2,
      status: "waiting_review",
      started_at: null,
      completed_at: null,
      gate_required: true,
      review_id: 21,
      baseline_id: null,
      owner_employee_id: 1,
      metadata_json: {},
      created_at: "2026-09-02T00:00:00Z",
      updated_at: "2026-09-02T00:00:00Z",
    },
  ],
  requirements: [],
  reviews: [],
  documents: [],
  baselines: [],
  change_requests: [],
  delivery_packages: [],
  pending_user_action: {
    kind: "review",
    title: "Requirements Review",
    review_id: 21,
    phase_id: 2,
  },
  coverage: { requirements: 100, design: 0, implementation: 0, tests: 0, acceptance: 0 },
  role_coverage_warning: "当前公司缺少独立 QA 角色；基础测试将由工程师承担。",
} satisfies ProjectLifecycle;

describe("ProjectLifecycleBoard", () => {
  it("makes the pending human gate the primary project action", () => {
    render(<ProjectLifecycleBoard lifecycle={lifecycle} />, { wrapper: MemoryRouter });
    expect(screen.getByText("ACTION REQUIRED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /进入评审|Enter review/i })).toHaveAttribute(
      "href",
      "/projects/9/reviews/21",
    );
    expect(screen.getByText(/缺少独立 QA/)).toBeInTheDocument();
  });
});
