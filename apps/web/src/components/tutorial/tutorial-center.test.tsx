import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PracticePreview, TutorialLibrary } from "../../api/tutorial";

/**
 * 教程库 / 实战入口。
 *
 * 这里要守住两件事：
 * 1. 开始 Classic Snake 之前，用户一定先看到“谁会用哪个模型、会不会产生模型费用”；
 * 2. 跳过实战只调 /practice/skip —— 不建项目、不派任务。
 */

const state = vi.hoisted(() => ({
  preview: null as PracticePreview | null,
  libraryStatus: "not_started" as string,
  skipPractice: vi.fn(),
  startPractice: vi.fn(),
  invalidate: vi.fn(),
}));

function libraryFixture(practiceStatus: "not_started" | "active" | "skipped"): TutorialLibrary {
  const definition = {
    id: "first-project-practice",
    version: 1,
    title_key: "tutorials.firstProjectPractice.title",
    description_key: "tutorials.firstProjectPractice.description",
    kind: "PRACTICE",
    sets_operating_stage: false,
    allow_skip: true,
    stages: [
      {
        id: "intake",
        title_key: "tutorials.stages.intake",
        steps: [
          {
            id: "create_project",
            kind: "REQUIRED_ACTION",
            requirement: "FIRST_PROJECT_CREATED",
            route: "/projects",
            target_id: "create-project",
            placement: "bottom",
            interaction_mode: "TARGET_ONLY",
            allow_skip: false,
            auto_advance: true,
            order: null,
            title_key: "steps.create_project.label",
            description_key: "steps.create_project.explanation",
            why_key: "steps.create_project.why",
            has_why: false,
            metadata: {},
          },
        ],
      },
    ],
  };
  const coreDefinition = { ...definition, id: "company-founding", allow_skip: false };
  const progress = (tutorial_id: string, current_step: string) => ({
    id: 1,
    user_id: 1,
    company_id: 1,
    tutorial_id,
    status: tutorial_id === "first-project-practice" ? practiceStatus : "active",
    current_stage: "intake",
    current_step,
    completed_steps: [],
    skipped_steps: [],
    context: {},
    started_at: null,
    completed_at: null,
    paused_at: null,
    created_at: "",
    updated_at: "",
  });
  return {
    library: [],
    center: [
      { id: "git", title_key: "tutorials.center.git", route: "/settings", replayable: true },
    ],
    tutorials: [
      { definition: coreDefinition, progress: progress("company-founding", "hire_ceo") } as never,
      {
        definition,
        progress: progress("first-project-practice", "create_project"),
      } as never,
    ],
  };
}

/** 忠实一点的 mutation 替身：真的回调 onSuccess，才能测到"成功后刷新查询"这条接线。 */
function mutationSpy(spy: (opts?: { onSuccess?: () => void }) => void) {
  return {
    mutate: (_variables: unknown, opts?: { onSuccess?: () => void }) => {
      spy(opts);
      opts?.onSuccess?.();
    },
    isPending: false,
  };
}

vi.mock("../../hooks/useTutorial", () => ({
  TUTORIAL_QUERY_KEY: ["tutorial"],
  useTutorialLibrary: () => ({ data: libraryFixture(state.libraryStatus as never) }),
  usePracticePreview: (open: boolean) => ({ data: open ? state.preview : undefined }),
  useSkipPractice: () => mutationSpy(state.skipPractice),
  useStartPractice: () => mutationSpy(state.startPractice),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: state.invalidate }),
  };
});

import { TutorialCenter } from "./tutorial-center";

beforeEach(() => {
  state.preview = null;
  state.skipPractice.mockClear();
  state.startPractice.mockClear();
  state.invalidate.mockClear();
});

describe("TutorialCenter", () => {
  it("核心与实战各自显示状态，互不牵连", () => {
    state.libraryStatus = "skipped";
    render(
      <MemoryRouter>
        <TutorialCenter />
      </MemoryRouter>,
    );
    const core = document.querySelector('[data-tutorial-card="company-founding"]');
    const practice = document.querySelector('[data-tutorial-card="first-project-practice"]');
    expect(
      core?.querySelector("[data-tutorial-card-status]")?.getAttribute("data-tutorial-card-status"),
    ).toBe("active");
    expect(
      practice
        ?.querySelector("[data-tutorial-card-status]")
        ?.getAttribute("data-tutorial-card-status"),
    ).toBe("skipped");
    expect(practice?.textContent).toContain("no project was created");
    // 已跳过用“重新开始”，未开始用“开始”——两种状态不能共用一句
    expect(screen.getByText("Restart practice")).toBeTruthy();
  });

  it("开始实战前先给真实成本预览：运行时 / 服务商 / 模型 + 费用警告", async () => {
    state.libraryStatus = "not_started";
    state.preview = {
      tutorial_id: "first-project-practice",
      template_name: "Classic Snake",
      tutorial_accelerated: true,
      uses_llm: true,
      mock_only: false,
      practice_status: "not_started",
      team: [
        {
          employee_id: 7,
          name: "Alice",
          role: "ceo",
          lifecycle_status: "active",
          runtime: "claude_code",
          provider: "Anthropic",
          model: "claude-test",
        },
      ],
    };
    render(
      <MemoryRouter>
        <TutorialCenter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Start practice"));
    expect(screen.getByText(/Accelerated mode/i)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Anthropic")).toBeTruthy());
    expect(screen.getByText("claude-test")).toBeTruthy();
    expect(screen.getByText(/incurs model fees/i)).toBeTruthy();
    expect(document.querySelector('[data-tutorial-cost="real"]')).not.toBeNull();
    expect(screen.getByText(/without spending anything/i)).toBeTruthy();
  });

  it("全是 Mock Runtime 时不虚报成本，也仍然要先确认才开始", async () => {
    state.libraryStatus = "not_started";
    state.preview = {
      tutorial_id: "first-project-practice",
      template_name: "Classic Snake",
      tutorial_accelerated: true,
      uses_llm: false,
      mock_only: true,
      practice_status: "not_started",
      team: [
        {
          employee_id: 8,
          name: "Bob",
          role: "engineer",
          lifecycle_status: "active",
          runtime: "mock",
          provider: null,
          model: null,
        },
      ],
    };
    render(
      <MemoryRouter>
        <TutorialCenter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Start practice"));
    await waitFor(() => expect(screen.getByText(/no model fees/i)).toBeTruthy());
    expect(document.querySelector('[data-tutorial-cost="mock"]')).not.toBeNull();

    fireEvent.click(screen.getByText("Start the project").closest("button")!);
    expect(state.startPractice).toHaveBeenCalled();
    expect(state.invalidate).toHaveBeenCalled();
  });

  it("跳过实战先确认，确认后只打 /practice/skip 这一个请求", () => {
    state.libraryStatus = "not_started";
    render(
      <MemoryRouter>
        <TutorialCenter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Skip practice for now"));
    // 确认之前不发请求：跳过必须是用户明确同意的零成本动作
    expect(state.skipPractice).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Skip anyway"));
    expect(state.skipPractice).toHaveBeenCalledTimes(1);
    expect(state.startPractice).not.toHaveBeenCalled();
  });
});
