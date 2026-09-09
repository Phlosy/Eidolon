/**
 * 教学卡片的体积碰撞避让（tutorial collision）。
 *
 * floating-ui 的 flip/shift 只负责"别飞出视口"，不会躲开"重要内容"
 * （聚光灯目标本身、打开的弹窗/向导）。这里用简单的 AABB 重叠面积
 * 给四个候选方位打分，挑"与保护区重叠最少"的方位 —— 像游戏里给
 * UI 算体模碰撞盒，不让提示卡片盖住用户下一步要点的按钮。
 */

export type TutorialPlacement = "top" | "bottom" | "left" | "right";

export interface RectLike {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SizeLike {
  width: number;
  height: number;
}

const GAP = 14;
const PADDING = 10;

/** 两个矩形的重叠面积（不重叠 → 0）。 */
export function rectOverlapArea(a: RectLike, b: RectLike): number {
  const x = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
  return x * y;
}

/** 按 placement 语义摆放面板（anchor 中心对齐 / 边缘 14px 间距），然后夹进视口。 */
export function panelRectFor(
  anchor: RectLike,
  panel: SizeLike,
  placement: TutorialPlacement,
  viewport: SizeLike,
): RectLike {
  const gap = GAP;
  let x: number;
  let y: number;
  switch (placement) {
    case "top":
      x = anchor.x + anchor.width / 2 - panel.width / 2;
      y = anchor.y - panel.height - gap;
      break;
    case "bottom":
      x = anchor.x + anchor.width / 2 - panel.width / 2;
      y = anchor.y + anchor.height + gap;
      break;
    case "left":
      x = anchor.x - panel.width - gap;
      y = anchor.y + anchor.height / 2 - panel.height / 2;
      break;
    case "right":
      x = anchor.x + anchor.width + gap;
      y = anchor.y + anchor.height / 2 - panel.height / 2;
      break;
  }
  const clamp = (value: number, extent: number, size: number) =>
    size >= extent - PADDING * 2
      ? PADDING
      : Math.min(Math.max(PADDING, value), extent - size - PADDING);
  return {
    x: clamp(x, viewport.width, panel.width),
    y: clamp(y, viewport.height, panel.height),
    width: panel.width,
    height: panel.height,
  };
}

/**
 * 选一个与保护区（聚光灯目标 + 弹窗等）重叠最少的方位。
 *
 * 规则：
 * - anchor 自身也算保护区（提示卡片绝不能盖住用户下一步要点的地方）；
 * - 全重叠时按 preferred → right → left → top → bottom 的顺序取，
 *   宁可边距差点也不盖住目标。
 */
export function choosePlacement(
  anchor: RectLike,
  panel: SizeLike,
  viewport: SizeLike,
  protectedRects: RectLike[],
  preferred: TutorialPlacement,
): TutorialPlacement {
  const guard = [anchor, ...protectedRects];
  const order: TutorialPlacement[] = (
    [preferred, "right", "left", "top", "bottom"] as TutorialPlacement[]
  ).filter((item, index, list) => list.indexOf(item) === index);
  let best: TutorialPlacement = order[0];
  let bestScore = Number.POSITIVE_INFINITY;
  for (const placement of order) {
    const rect = panelRectFor(anchor, panel, placement, viewport);
    const score = guard.reduce(
      (sum, protectedRect) => sum + rectOverlapArea(rect, protectedRect),
      0,
    );
    if (score < bestScore) {
      best = placement;
      bestScore = score;
      if (score === 0) break; // 完全不重叠就是最优，不用再看
    }
  }
  return best;
}
