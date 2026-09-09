import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TutorialLibrary } from "../../api/tutorial";

/**
 * 核心教程通关后的交接卡片。
 *
 * 守住三件事：
 * 1. 核心完成、实战未开始时才出现；
 * 2. 实战已在走时不能再打扰；
 * 3. 关掉之后刷新不再回来（localStorage）。
 */

const state = vi.hoisted(() => ({
  coreStatus: "completed" as string,
  practiceStatus: "not_started" as string,
  startPractice: vi.fn(),
}));

function libraryFixture(): TutorialLibrary {
  const definition = (id: string) =>
    ({
      id,
      version: 1,
      title_key: "tutorials.companyFounding.title",
      kind: "REQUIRED_ACTION",
      sets_operating_stage: false,
      allow_skip: false,
      stages: [],
    }) as never;
  const progress = (tutorial_id: string, status: string) =>
    ({
      id: 1,
      user_id: 1,
      company_id: 1,
      tutorial_id,
      status,
      current_stage: "completed",
      current_step: "completed",
      completed_steps: [],
      skipped_steps: [],
      context: {},
      started_at: null,
      completed_at: null,
      paused_at: null,
      created_at: "",
      updated_at: "",
    }) as never;
  return {
    library: [],
    center: [],
    tutorials: [
      {
        definition: definition("company-founding"),
        progress: progress("company-founding", state.coreStatus),
      },
      {
        definition: definition("first-project-practice"),
        progress: progress("first-project-practice", state.practiceStatus),
      },
    ],
  };
}

vi.mock("../../hooks/useTutorial", () => ({
  useTutorialLibrary: () => ({ data: libraryFixture() }),
  useStartPractice: () => ({ mutate: state.startPractice, isPending: false }),
  usePracticePreview: () => ({ data: undefined }),
}));

import { TutorialHandoff } from "./tutorial-handoff";

function renderHandoff() {
  return render(
    <MemoryRouter>
      <TutorialHandoff />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  state.coreStatus = "completed";
  state.practiceStatus = "not_started";
  state.startPractice.mockClear();
  window.localStorage.clear();
});

describe("TutorialHandoff", () => {
  it("核心通关且实战未开始时显示交接卡片", () => {
    renderHandoff();
    expect(screen.getByText("Your company can run now")).toBeTruthy();
    expect(screen.getByText("Start the project practice")).toBeTruthy();
  });

  it("实战已在走时不显示", () => {
    state.practiceStatus = "active";
    renderHandoff();
    expect(screen.queryByText("Your company can run now")).toBeNull();
  });

  it("核心还没完成时不显示", () => {
    state.coreStatus = "active";
    renderHandoff();
    expect(screen.queryByText("Your company can run now")).toBeNull();
  });

  it("关掉之后写进 localStorage，刷新不再出现", () => {
    renderHandoff();
    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByText("Your company can run now")).toBeNull();
    expect(window.localStorage.getItem("eidolon.tutorial.practiceHandoff.dismissed")).toBe("1");
    renderHandoff();
    expect(screen.queryByText("Your company can run now")).toBeNull();
  });

  it("点开始项目实战先打开成本确认弹窗，不直接开始", () => {
    renderHandoff();
    fireEvent.click(screen.getByText("Start the project practice"));
    expect(screen.getByText("Start the first project practice")).toBeTruthy();
    expect(state.startPractice).not.toHaveBeenCalled();
  });
});
