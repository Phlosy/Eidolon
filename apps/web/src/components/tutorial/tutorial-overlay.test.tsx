import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TutorialDefinition, TutorialProgress, TutorialStep } from "../../api/tutorial";

/**
 * Interactive Spotlight 教程的引擎行为测试。
 *
 * 刻意 mock 在 hooks 层：进度与定义是后端的事实，前端要证明的是
 * "给它一个状态，它就渲染出正确的光、正确的面板、正确的按钮"，
 * 而不是重复验证后端门禁。
 */

const step = (overrides: Partial<TutorialStep>): TutorialStep => ({
  id: "x",
  kind: "REQUIRED_ACTION",
  requirement: "R",
  route: "/employees",
  target_id: "hire-employee",
  placement: "bottom",
  interaction_mode: "TARGET_ONLY",
  allow_skip: false,
  auto_advance: true,
  order: null,
  title_key: "steps.hire_ceo.label",
  description_key: "steps.hire_ceo.explanation",
  why_key: "steps.hire_ceo.why",
  has_why: false,
  metadata: {},
  ...overrides,
});

const DEFINITION: TutorialDefinition = {
  id: "company-founding",
  version: 2,
  title_key: "tutorials.companyFounding.title",
  kind: "REQUIRED_ACTION",
  sets_operating_stage: true,
  allow_skip: false,
  stages: [
    {
      id: "welcome",
      title_key: "tutorials.stages.welcome",
      steps: [
        step({
          id: "company_setup",
          kind: "INFORMATION",
          route: "/",
          target_id: "company-overview",
          interaction_mode: "FOCUS_ONLY",
          allow_skip: true,
        }),
        step({ id: "hire_ceo", requirement: "CEO_ACTIVE" }),
        step({
          id: "git_setup",
          kind: "OPTIONAL_ACTION",
          route: "/settings",
          target_id: "git-connection-create",
          interaction_mode: "NON_BLOCKING",
          allow_skip: true,
        }),
      ],
    },
  ],
};

const state = vi.hoisted(() => ({
  progress: null as TutorialProgress | null,
  // 每个用例自带定义：共享对象被就地改会跨用例污染（这里真的踩过）
  definition: null as unknown,
  complete: vi.fn(),
  skip: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
}));

function progressFor(currentStep: string, overrides: Partial<TutorialProgress> = {}) {
  state.progress = {
    id: 1,
    user_id: 1,
    company_id: 1,
    tutorial_id: "company-founding",
    status: "active",
    current_stage: "welcome",
    current_step: currentStep,
    completed_steps: [],
    skipped_steps: [],
    context: {},
    started_at: null,
    completed_at: null,
    paused_at: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

vi.mock("../../hooks/useTutorial", () => ({
  TUTORIAL_QUERY_KEY: ["tutorial"],
  useTutorial: () => ({ data: state.progress }),
  useTutorialDefinition: () => ({ data: (state.definition ?? DEFINITION) as TutorialDefinition }),
  useCompleteTutorialStep: () => ({ mutate: state.complete, isPending: false, error: null }),
  useSkipTutorialStep: () => ({ mutate: state.skip, isPending: false, error: null }),
  usePauseTutorial: () => ({ mutate: state.pause, isPending: false }),
  useResumeTutorial: () => ({ mutate: state.resume, isPending: false }),
}));

import { TutorialOverlay } from "./tutorial-overlay";
import { startReplay, stopReplay } from "./tutorial-replay";

function rectStub() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const [x = 0, y = 0, w = 0, h = 0] = (this.dataset.rect ?? "0,0,0,0")
      .split(",")
      .map((value) => Number(value) || 0);
    return {
      x,
      y,
      width: w,
      height: h,
      top: y,
      left: x,
      bottom: y + h,
      right: x + w,
      toJSON: () => ({}),
    } as DOMRect;
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/"
          element={
            <div>
              <section data-tutorial-target="company-overview" data-rect="40,60,400,300">
                home
              </section>
              <TutorialOverlay />
            </div>
          }
        />
        <Route
          path="/employees"
          element={
            <div>
              <button data-tutorial-target="hire-employee" data-rect="900,120,120,36">
                Hire
              </button>
              <TutorialOverlay />
            </div>
          }
        />
        <Route
          path="/settings"
          element={
            <div>
              settings page
              <TutorialOverlay />
            </div>
          }
        />
        <Route path="*" element={<div>other page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function spotlight(): HTMLElement | null {
  return document.querySelector('[data-tutorial-overlay="spotlight"]');
}

beforeEach(() => {
  state.definition = null;
  rectStub();
  document.body.innerHTML = "";
  state.complete.mockClear();
  state.skip.mockClear();
  state.pause.mockClear();
  state.resume.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
  stopReplay();
});

describe("信息步骤与动作步骤的区别", () => {
  it("INFORMATION 有『知道了』，点击后才推进", () => {
    progressFor("company_setup");
    renderAt("/");
    const button = screen.getByText("Got it");
    fireEvent.click(button);
    expect(state.complete).toHaveBeenCalledWith("company_setup");
  });

  it("REQUIRED_ACTION 不给任何“下一步”式的完成入口（§39）", () => {
    progressFor("hire_ceo");
    renderAt("/employees");
    expect(screen.queryByText("Got it")).toBeNull();
    expect(screen.queryByText("Skip step")).toBeNull();
    // 面板只解释"完成即自动前进"，不给任何可以冒充完成的按钮
    expect(screen.getByText(/no Next button needed/i)).toBeTruthy();
    expect(screen.getByText(/cannot be completed by clicking/i)).toBeTruthy();
    expect(state.complete).not.toHaveBeenCalled();
  });

  it("OPTIONAL_ACTION 才显示稍后再说，并打给后端 skip", () => {
    progressFor("git_setup");
    renderAt("/settings");
    fireEvent.click(screen.getByText("Later"));
    expect(state.skip).toHaveBeenCalledWith("git_setup");
  });
});

describe("遮罩与 interaction_mode", () => {
  it("TARGET_ONLY：挖孔外的四块遮罩吃掉点击", () => {
    progressFor("hire_ceo");
    renderAt("/employees");
    const layer = spotlight();
    expect(layer).not.toBeNull();
    const dims = layer!.querySelectorAll('[data-tutorial-dim="true"]');
    expect(dims.length).toBe(4);
    expect((dims[0] as HTMLElement).style.pointerEvents).toBe("auto");
    expect(layer!.getAttribute("data-tutorial-blocking")).toBe("true");
  });

  it("NON_BLOCKING 不铺遮罩：教程不能把整个应用锁死", () => {
    progressFor("git_setup");
    renderAt("/settings");
    expect(spotlight()).toBeNull();
  });

  it("FOCUS_ONLY 打光但不拦点击", () => {
    progressFor("company_setup");
    renderAt("/");
    const layer = spotlight();
    expect(layer).not.toBeNull();
    expect(layer!.getAttribute("data-tutorial-blocking")).toBe("false");
    expect(
      (layer!.querySelector('[data-tutorial-dim="true"]') as HTMLElement).style.pointerEvents,
    ).toBe("none");
  });

  it("视口变化后，光斑跟着目标重算", async () => {
    progressFor("hire_ceo");
    renderAt("/employees");
    const before = spotlight()!.querySelector('[data-tutorial-halo="true"]') as HTMLElement;
    expect(before.style.left).toBe("892px");

    const button = document.querySelector('[data-tutorial-target="hire-employee"]') as HTMLElement;
    button.dataset.rect = "420,300,120,36";
    window.dispatchEvent(new Event("resize"));
    fireEvent(window, new Event("scroll"));

    await waitFor(() => {
      const after = document.querySelector('[data-tutorial-halo="true"]') as HTMLElement;
      expect(after.style.left).toBe("412px");
      expect(after.style.top).toBe("292px");
    });
  });
});

describe("兜底：目标找不到时不瞎指", () => {
  it("有弹窗开着时不抢导航：给出“打开这一步”让用户自己决定", () => {
    progressFor("hire_ceo");
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    document.body.appendChild(dialog);
    renderAt("/settings");
    expect(screen.getByText("Open this step")).toBeTruthy();
    expect(spotlight()).toBeNull();
    // 仍然没有被自动带到 /employees
    expect(screen.queryByText("Hire")).toBeNull();
  });

  it("在目标页面但元素被移除 → 明确说找不到，并给手动入口", async () => {
    progressFor("hire_ceo");
    renderAt("/employees");
    expect(spotlight()).not.toBeNull();
    document.querySelector('[data-tutorial-target="hire-employee"]')!.remove();
    // 不重新渲染：靠 MutationObserver 自己发现"目标没了"
    await waitFor(() =>
      expect(document.querySelector('[data-tutorial-fallback="true"]')).not.toBeNull(),
    );
    expect(screen.getByText(/Could not find the element/i)).toBeTruthy();
    expect(screen.getByText("Open the page manually")).toBeTruthy();
    expect(screen.getByText("Relocate")).toBeTruthy();
    expect(spotlight()).toBeNull();
  });

  it("route 占位符未解析时提示“还差一个真实对象”，且不会自动跳转", () => {
    progressFor("hire_ceo");
    state.progress!.context = {};
    // 用一条带占位符的步骤模拟"员工还没创建"
    state.definition = {
      ...DEFINITION,
      stages: [
        {
          ...DEFINITION.stages[0],
          steps: [
            DEFINITION.stages[0].steps[0],
            step({
              id: "hire_ceo",
              route: "/employees/{ceo_employee_id}",
              target_id: "employee-runtime-tab",
            }),
            DEFINITION.stages[0].steps[2],
          ],
        },
      ],
    };
    renderAt("/");
    expect(screen.getByText("A real object is still missing")).toBeTruthy();
    expect(spotlight()).toBeNull();
  });
});

describe("跨路由", () => {
  it("步骤在别的页面时，教程自己导航过去", async () => {
    progressFor("hire_ceo");
    renderAt("/");
    await waitFor(() => expect(screen.getByText("Hire")).toBeTruthy());
  });

  it("每一步最多自动跳转一次，之后不再抢方向盘", async () => {
    progressFor("hire_ceo");
    renderAt("/");
    await waitFor(() => expect(screen.getByText("Hire")).toBeTruthy());
    // 用户自己走开：教程不该再把人拽回来（同一状态再渲染一轮）
    fireEvent.click(screen.getByText("Hire"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("other page")).toBeNull();
  });
});

describe("向导内部指引（ui_hints）", () => {
  const wizardStep = step({
    id: "hire_ceo",
    requirement: "CEO_ACTIVE",
    route: "/employees",
    target_id: "hire-employee",
    metadata: {
      ui_hints: [
        { target_id: "wizard-identity", text_key: "hints.identity" },
        { target_id: "wizard-confirm", text_key: "hints.confirmOnboard" },
      ],
    },
  });

  beforeEach(() => {
    state.definition = {
      ...DEFINITION,
      stages: [
        {
          ...DEFINITION.stages[0],
          steps: [DEFINITION.stages[0].steps[0], wizardStep, DEFINITION.stages[0].steps[2]],
        },
      ],
    };
  });

  it("招聘向导打开时，光跟随向导内部元素，而不是抱怨被弹窗挡住", async () => {
    progressFor("hire_ceo");
    render(
      <MemoryRouter initialEntries={["/employees"]}>
        <Routes>
          <Route
            path="/employees"
            element={
              <div>
                <div role="dialog" aria-modal="true" data-rect="100,50,500,600">
                  <div data-tutorial-target="wizard-identity" data-rect="120,90,300,40">
                    identity
                  </div>
                  <button data-tutorial-target="wizard-confirm" data-rect="420,560,120,32">
                    Hire
                  </button>
                </div>
                <TutorialOverlay />
              </div>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/Confirm identity first/i)).toBeTruthy());
    // 第一条指引打在 wizard-identity 上
    const halo = () => document.querySelector('[data-tutorial-halo="true"]') as HTMLElement;
    expect(halo().style.left).toBe("112px");
    expect(screen.getByText(/Hints only nudge/i)).toBeTruthy();

    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => expect(screen.getByText(/Review and hire/i)).toBeTruthy());
    expect(halo().style.left).toBe("412px");

    // 指引态不能拦点击：向导自己的"下一步"必须还能按（实测被遮罩锁死过）
    const dims = document.querySelectorAll('[data-tutorial-dim="true"]');
    expect(dims.length).toBeGreaterThan(0);
    expect([...dims].every((d) => (d as HTMLElement).style.pointerEvents === "none")).toBe(true);

    // 翻指引绝不会伪装成完成：没有任何进度 mutation
    expect(state.complete).not.toHaveBeenCalled();
    expect(state.skip).not.toHaveBeenCalled();
    expect(document.querySelector('[data-tutorial-fallback="true"]')).toBeNull();
  });

  it("指引走到最后一条时“下一条”禁用，但步骤仍等真实状态", () => {
    progressFor("hire_ceo");
    render(
      <MemoryRouter initialEntries={["/employees"]}>
        <Routes>
          <Route
            path="/employees"
            element={
              <div role="dialog" aria-modal="true" data-rect="0,0,400,400">
                <div data-tutorial-target="wizard-identity" data-rect="10,10,60,20">
                  a
                </div>
                <div data-tutorial-target="wizard-confirm" data-rect="10,50,60,20">
                  b
                </div>
                <TutorialOverlay />
              </div>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    const next = screen.getByText("Next").closest("button")!;
    fireEvent.click(next);
    expect(next.disabled).toBe(true);
    expect(state.complete).not.toHaveBeenCalled();
  });
});

describe("暂停与回放", () => {
  it("paused 只显示入口，不遮挡页面", () => {
    progressFor("hire_ceo", { status: "paused" });
    renderAt("/employees");
    expect(document.querySelector('[data-tutorial-overlay="paused"]')).not.toBeNull();
    expect(spotlight()).toBeNull();
    fireEvent.click(screen.getByText("Resume tutorial"));
    expect(state.resume).toHaveBeenCalled();
  });

  it("回放模式只做本地走查，不发任何进度请求", () => {
    state.progress = null;
    startReplay(DEFINITION, 0);
    renderAt("/");
    expect(screen.getByText("Replay")).toBeTruthy();
    fireEvent.click(screen.getByText("Next"));
    expect(screen.getByText(/Replay walks the explanations only/i)).toBeTruthy();
    expect(state.complete).not.toHaveBeenCalled();
    expect(state.skip).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Exit replay" }));
    expect(screen.queryByText("Replay")).toBeNull();
  });
});
