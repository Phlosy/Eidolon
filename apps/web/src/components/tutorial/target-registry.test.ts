import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  findTargetElement,
  isElementVisible,
  readTargetSnapshot,
  tutorialTargets,
} from "./target-registry";

/**
 * Target Registry 是教程"看得见指得准"的地基，所以这里测的是定位策略本身，
 * 而不是某个组件的渲染结果。
 *
 * jsdom 的 getBoundingClientRect 恒为 0×0，而"0 尺寸"在我们的语义里就是不可见
 * —— 所以用一个 data-rect 属性驱动的全局替身来伪造真实布局。
 */

function installRects() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const [x = 0, y = 0, w = 0, h = 0] = (this.dataset.rect ?? "")
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

function mount(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.body;
}

beforeEach(() => {
  installRects();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("readTargetSnapshot", () => {
  it("报告 visible 并带上真实矩形", () => {
    mount('<button data-tutorial-target="hire-employee" data-rect="120,40,80,32">Hire</button>');
    const snapshot = readTargetSnapshot({ id: "hire-employee" });
    expect(snapshot.status).toBe("visible");
    expect(snapshot.rect?.width).toBe(80);
    expect(snapshot.rect?.top).toBe(40);
  });

  it("元素不存在 → missing（引擎据此走兜底面板）", () => {
    mount("<div>nothing here</div>");
    expect(readTargetSnapshot({ id: "create-document" }).status).toBe("missing");
  });

  it("祖先 display:none → hidden，而不是谎称找不到", () => {
    mount(
      '<div style="display:none"><button data-tutorial-target="employee-runtime-tab" data-rect="10,10,50,20">Runtime</button></div>',
    );
    const snapshot = readTargetSnapshot({ id: "employee-runtime-tab" });
    expect(snapshot.status).toBe("hidden");
    expect(snapshot.element).not.toBeNull();
  });

  it("0 尺寸也算 hidden（折叠面板里的元素）", () => {
    mount('<button data-tutorial-target="phase-development" data-rect="5,5,0,0">Dev</button>');
    expect(readTargetSnapshot({ id: "phase-development" }).status).toBe("hidden");
  });

  it("弹窗开着而目标在弹窗外 → covered", () => {
    mount(`
      <button data-tutorial-target="git-connection-create" data-rect="5,5,60,24">git</button>
      <div role="dialog" aria-modal="true" data-rect="0,0,300,300"><span>wizard</span></div>
    `);
    expect(readTargetSnapshot({ id: "git-connection-create" }).status).toBe("covered");
  });

  it("目标在弹窗内部 → 照常 visible（向导内部也要能打光）", () => {
    mount(`
      <div role="dialog" aria-modal="true" data-rect="0,0,300,300">
        <button data-tutorial-target="wizard-confirm" data-rect="20,200,80,30">Hire</button>
      </div>
    `);
    expect(readTargetSnapshot({ id: "wizard-confirm" }).status).toBe("visible");
  });
});

describe("findTargetElement 选择策略", () => {
  it("迁移期同时认旧的 data-tutorial", () => {
    mount('<section data-tutorial="company-overview" data-rect="0,0,200,200"></section>');
    expect(findTargetElement({ id: "company-overview" })).not.toBeNull();
  });

  it("同页多个同名 target：按 data-tutorial-key 精确命中", () => {
    mount(`
      <button data-tutorial-target="employee-runtime-tab" data-tutorial-key="ceo" data-rect="0,0,60,20">ceo</button>
      <button data-tutorial-target="employee-runtime-tab" data-tutorial-key="engineer" data-rect="0,40,60,20">eng</button>
    `);
    const picked = findTargetElement({ id: "employee-runtime-tab", key: "engineer" });
    expect(picked?.getAttribute("data-tutorial-key")).toBe("engineer");
  });

  it("要求 key 但没有命中时返回 null —— 绝不能退化成指错人", () => {
    mount(
      '<button data-tutorial-target="employee-runtime-tab" data-tutorial-key="ceo" data-rect="0,0,60,20">ceo</button>',
    );
    expect(findTargetElement({ id: "employee-runtime-tab", key: "qa" })).toBeNull();
  });

  it("多个候选时优先可见的那个", () => {
    mount(`
      <div style="display:none"><button data-tutorial-target="x" data-rect="0,0,10,10">hidden</button></div>
      <button data-tutorial-target="x" data-rect="0,0,10,10">shown</button>
    `);
    const picked = findTargetElement({ id: "x" });
    expect(picked?.parentElement?.style.display).not.toBe("none");
    expect(isElementVisible(picked!)).toBe(true);
  });
});

describe("订阅：DOM 与视口变化", () => {
  it("目标稍后才渲染出来时会补发快照", async () => {
    mount("<div id='slot'></div>");
    const seen: string[] = [];
    const unsubscribe = tutorialTargets.subscribe({ id: "late-target" }, (snapshot) =>
      seen.push(snapshot.status),
    );
    expect(seen).toEqual(["missing"]);

    document.getElementById("slot")!.innerHTML =
      '<button data-tutorial-target="late-target" data-rect="4,4,40,40">later</button>';
    await waitFor(() => expect(seen).toContain("visible"));
    unsubscribe();
  });

  it("resize 改变位置后订阅者拿到新矩形（§窗口大小改变后遮罩仍然正确）", async () => {
    mount('<button data-tutorial-target="create-project" data-rect="10,10,60,24">New</button>');
    const rects: number[] = [];
    const unsubscribe = tutorialTargets.subscribe({ id: "create-project" }, (snapshot) =>
      rects.push(Math.round(snapshot.rect?.left ?? -1)),
    );
    expect(rects.at(-1)).toBe(10);

    const button = document.querySelector("[data-tutorial-target]") as HTMLElement;
    button.dataset.rect = "300,500,60,24";
    window.dispatchEvent(new Event("resize"));
    await waitFor(() => expect(rects.at(-1)).toBe(300));
    unsubscribe();
  });

  it("取消订阅后不再被打扰", async () => {
    mount('<button data-tutorial-target="gone" data-rect="1,1,20,20">x</button>');
    const listener = vi.fn();
    const unsubscribe = tutorialTargets.subscribe({ id: "gone" }, listener);
    // subscribe 会先同步给一次当前值（React 首帧不能是空的），那是合法调用
    const initialCalls = listener.mock.calls.length;
    expect(initialCalls).toBe(1);
    unsubscribe();

    document.body.innerHTML =
      '<button data-tutorial-target="gone" data-rect="9,9,20,20">moved</button>';
    window.dispatchEvent(new Event("resize"));
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(listener.mock.calls.length).toBe(initialCalls);
  });
});
