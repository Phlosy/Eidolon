import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreateProjectInput, Employee } from "../../types";

/**
 * 立项向导：实战教程打开时，Classic Snake 示例数据必须自动预填，
 * 用户只需要审阅，不需要自己去点「载入教学项目」。
 */

const state = vi.hoisted(() => ({
  practicing: true,
  createProject: vi.fn(),
}));

const TEMPLATE_INTAKE: CreateProjectInput = {
  name: "Classic Snake",
  code: "SNAKE",
  priority: "high",
  customer: "Eidolon Tutorial",
  background: "用于验证完整项目生命周期。",
  objectives: ["交付一个可快速运行、可验收的贪吃蛇小游戏"],
  requirements: [
    {
      code: "REQ-001",
      title: "游戏区域",
      description: "显示游戏区域",
      priority: "must",
      acceptance_criteria: "进入页面可见清晰网格",
    },
  ],
  technical_requirements: ["React", "Vite"],
  constraints: ["支持离线部署"],
  deliverables: ["Source Code", "Build Package"],
  milestones: [{ name: "需求基线确认" }],
  review_configuration: {
    requirements_review: true,
    design_review: true,
    acceptance_review: true,
    additional_reviews: [],
  },
  participants: {
    customer_contact: "",
    project_owner_employee_id: null,
    presenter_employee_id: null,
    reviewer_names: [],
    approver_names: [],
  },
  tutorial_accelerated: true,
};

function makeEmployee(id: number, name: string, role: Employee["role"]): Employee {
  return { id, name, role } as Employee;
}

vi.mock("../../hooks/useProjects", () => ({
  useCreateProject: () => ({ mutate: state.createProject, isPending: false }),
}));
vi.mock("../../hooks/useEmployees", () => ({
  useEmployees: () => ({
    data: [makeEmployee(7, "Ada", "ceo"), makeEmployee(8, "Tom", "engineer")],
  }),
}));
vi.mock("../../hooks/useTutorial", () => ({
  usePractice: () => ({
    data: { progress: { status: state.practicing ? "active" : "not_started" } },
  }),
  useClassicSnakeTemplate: () => ({
    data: { name: "Classic Snake", intake: TEMPLATE_INTAKE },
  }),
}));

import { ProjectIntakeWizard } from "./project-intake-wizard";

function renderWizard() {
  return render(
    <MemoryRouter>
      <ProjectIntakeWizard open onOpenChange={() => {}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  state.practicing = true;
  state.createProject.mockClear();
  window.localStorage.clear();
});

describe("ProjectIntakeWizard", () => {
  it("实战教程打开时自动预填示例项目，并提示只需审阅", async () => {
    renderWizard();
    expect(await screen.findByDisplayValue("Classic Snake")).toBeTruthy();
    expect(screen.getByDisplayValue("SNAKE")).toBeTruthy();
    expect(screen.getByText(/pre-filled from the Classic Snake sample/i)).toBeTruthy();
    // 负责人默认是 CEO
    expect(screen.getByDisplayValue("Ada · ceo")).toBeTruthy();
  });

  it("非实战状态下不自动套用示例数据", () => {
    state.practicing = false;
    renderWizard();
    expect(screen.queryByDisplayValue("Classic Snake")).toBeNull();
  });

  it("有草稿时优先草稿，不覆盖用户之前的修改", async () => {
    window.localStorage.setItem(
      "eidolon-project-intake-draft-v1",
      JSON.stringify({ ...TEMPLATE_INTAKE, name: "My Edited Snake" }),
    );
    renderWizard();
    expect(await screen.findByDisplayValue("My Edited Snake")).toBeTruthy();
    expect(screen.queryByDisplayValue("Classic Snake")).toBeNull();
  });
});
