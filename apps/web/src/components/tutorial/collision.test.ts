import { describe, expect, it } from "vitest";
import { choosePlacement, panelRectFor, rectOverlapArea, type RectLike } from "./collision";

const VIEWPORT = { width: 1200, height: 800 };
const PANEL = { width: 380, height: 200 };

/** 右上角按钮区（新建/上传那排）—— teaching card 绝不能盖住它们。 */
const TOOLBAR_BUTTON: RectLike = { x: 950, y: 60, width: 44, height: 44 };

describe("tutorial collision", () => {
  it("rectOverlapArea: 重叠算面积、不重叠为 0", () => {
    expect(
      rectOverlapArea({ x: 0, y: 0, width: 10, height: 10 }, { x: 5, y: 5, width: 10, height: 10 }),
    ).toBe(25);
    expect(
      rectOverlapArea({ x: 0, y: 0, width: 5, height: 5 }, { x: 20, y: 20, width: 5, height: 5 }),
    ).toBe(0);
  });

  it("panelRectFor: 按方位摆好并夹进视口", () => {
    const bottom = panelRectFor(TOOLBAR_BUTTON, PANEL, "bottom", VIEWPORT);
    expect(bottom.y).toBeGreaterThanOrEqual(TOOLBAR_BUTTON.y + TOOLBAR_BUTTON.height);
    expect(bottom.x + bottom.width).toBeLessThanOrEqual(VIEWPORT.width - 10);
    const left = panelRectFor(TOOLBAR_BUTTON, PANEL, "left", VIEWPORT);
    expect(left.x + left.width).toBeLessThanOrEqual(TOOLBAR_BUTTON.x);
  });

  it("首选方位不遮目标 → 保持不动（右上角按钮 bottom 是安全的）", () => {
    const picked = choosePlacement(TOOLBAR_BUTTON, PANEL, VIEWPORT, [], "bottom");
    expect(picked).toBe("bottom");
  });

  it("首选方位会盖住目标（目标靠底）→ 体积碰撞必须换到侧边", () => {
    // 目标贴着视口底部：bottom 面板被迫上抬盖住目标 —— 此时必须避开
    const bottomEdge: RectLike = { x: 600, y: 600, width: 100, height: 40 };
    const picked = choosePlacement(bottomEdge, PANEL, VIEWPORT, [], "bottom");
    expect(picked).not.toBe("bottom");
    const rect = panelRectFor(bottomEdge, PANEL, picked, VIEWPORT);
    expect(rectOverlapArea(rect, bottomEdge)).toBe(0);
  });

  it("首选方位不挡目标 → 保持不变（不动布局）", () => {
    const centerTarget: RectLike = { x: 500, y: 300, width: 100, height: 40 };
    // top/bottom/left/right 都不碰这个目标时，保持 preferred
    const picked = choosePlacement(centerTarget, PANEL, VIEWPORT, [], "bottom");
    expect(picked).toBe("bottom");
  });

  it("弹窗在下方时选上方，别盖住向导/对话框", () => {
    const target: RectLike = { x: 500, y: 100, width: 100, height: 40 };
    const dialogBelow: RectLike = { x: 300, y: 220, width: 520, height: 400 };
    // preferred bottom 会压到弹窗 → 应该选 top
    const picked = choosePlacement(target, PANEL, VIEWPORT, [dialogBelow], "bottom");
    // bottom 会压到弹窗，top 会盖住目标本身，唯一完全无碰撞的是 right
    expect(picked).toBe("right");
    expect(rectOverlapArea(panelRectFor(target, PANEL, picked, VIEWPORT), dialogBelow)).toBe(0);
  });

  it("超大目标（整屏）→ 退到哪个方位都重叠时也夹进视口", () => {
    const huge: RectLike = { x: 0, y: 0, width: 1100, height: 700 };
    const picked = choosePlacement(huge, PANEL, VIEWPORT, [], "right");
    const rect = panelRectFor(huge, PANEL, picked, VIEWPORT);
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.y).toBeGreaterThanOrEqual(0);
  });
});
