import { arrow, autoUpdate, computePosition, flip, offset, shift } from "@floating-ui/dom";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { useLayoutEffect, useRef, useState } from "react";
import type { TutorialPlacement } from "../../api/tutorial";
import { choosePlacement } from "./collision";

/**
 * 教练面板：贴在真实目标旁边的说明卡片。
 *
 * 为什么不自己算 top/left：目标可能贴边、可能被 Tab 裁切、面板本身还要放
 * "为什么这一步重要"的展开内容。Floating UI 的 flip/shift 会把它拉回可视区，
 * 而 autoUpdate 负责滚动与 resize 期间持续跟随 —— 这正是 §"窗口大小改变后
 * 遮罩位置仍然正确"要求的行为。
 */

const PADDING = 12;

const FALLBACK_CENTER_STYLE = {
  left: "50%",
  top: "50%",
  transform: "translate(-50%, -50%)",
} as const;

interface CoachPanelProps {
  /** 目标矩形；null 表示没有可依附的目标（引擎此时渲染兜底态） */
  anchor: DOMRect | null;
  /**
   * 惰性锚点：每次摆放时重新读取。弹窗锚点必须用这个 —— 弹窗内容会自己
   * 长高/移位（如向导里展开"新服务商"表单），而弹窗内部控件此刻可能全是
   * missing（snapshot 无变化 → overlay 不重渲染），render 期捕获的矩形会过期，
   * 卡片就停在旧位置压住新弹窗（实测 provider 子步骤重叠 84170px²）。
   */
  getAnchor?: () => DOMRect | null;
  placement: TutorialPlacement;
  children: ReactNode;
  /** 兜底态用更醒目的描边，让用户知道"教程没瞎指，是找不到东西" */
  degraded?: boolean;
}

function rectKey(rect: DOMRect | null): string {
  if (!rect) return "none";
  return `${Math.round(rect.left)},${Math.round(rect.top)},${Math.round(rect.width)}x${Math.round(rect.height)}`;
}

export function CoachPanel({ anchor, getAnchor, placement, children, degraded }: CoachPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const arrowRef = useRef<HTMLDivElement>(null);
  // 坐标只走 state → style prop。之前混用了"React 的 style prop"和
  // "place() 里直接写 panel.style"，React 在下一帧把 left/top 当成自己记账的
  // 属性抹掉了，面板会瞬间跳回静态位置（实测：掉到视口下方 903px）。
  const [coords, setCoords] = useState<{
    x: number;
    y: number;
    arrowX?: number;
    arrowY?: number;
  } | null>(null);
  const [applied, setApplied] = useState<TutorialPlacement | string>(placement);
  const key = rectKey(anchor);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) {
      setCoords(null);
      return undefined;
    }
    // 惰性锚点优先：每次摆放都读最新矩形；没有惰性读取才用冻结快照
    const readAnchor = () => getAnchor?.() ?? anchor;
    if (!readAnchor()) {
      setCoords(null);
      return undefined;
    }
    const reference = { getBoundingClientRect: () => readAnchor()! };
    let cancelled = false;
    const place = async () => {
      const currentAnchor = readAnchor();
      if (!currentAnchor) {
        if (!cancelled) setCoords(null);
        return;
      }
      // 体积碰撞避让：先自己按"与保护区重叠最少"选方位，再交给 floating-ui
      // 做视口内微调（flip 改用空回退，防止它翻回遮挡侧）。
      // 保护区 = 聚光灯目标 + 打开的弹窗 + 页面上声明 data-tutorial-protected
      // 的关键内容区（哪个元素不该被卡片盖住，由页面自己声明）。
      const viewW = document.documentElement.clientWidth;
      const viewH = document.documentElement.clientHeight;
      const protectedRects = [
        currentAnchor,
        ...Array.from(
          document.querySelectorAll<HTMLElement>(
            '[role="dialog"][aria-modal="true"], [data-tutorial-protected]',
          ),
        ).map((el) => el.getBoundingClientRect()),
      ];
      const panelSize = { width: panel.offsetWidth || 380, height: panel.offsetHeight || 200 };
      const picked = choosePlacement(
        currentAnchor,
        panelSize,
        { width: viewW, height: viewH },
        protectedRects,
        placement === "auto" ? "bottom" : placement,
      );
      const {
        x: rawX,
        y: rawY,
        placement: appliedPlacement,
        middlewareData,
      } = await computePosition({ getBoundingClientRect: () => currentAnchor }, panel, {
        placement: picked,
        strategy: "fixed",
        middleware: [
          offset(14),
          flip({ padding: PADDING, fallbackPlacements: [] }),
          shift({ padding: PADDING }),
          ...(arrowRef.current ? [arrow({ element: arrowRef.current })] : []),
        ],
      });
      if (cancelled) return;
      // 确定性兜底：参考元素几乎占满视口时（例如整个公司总览面板），flip/shift 会
      // 算出 x = -98 这种屏幕外坐标 —— 实测过。自己夹一遍，保证面板永远在屏内，
      // 兑现 §"窗口大小改变后遮罩与面板位置仍然正确"。
      const viewWidth = document.documentElement.clientWidth;
      const viewHeight = document.documentElement.clientHeight;
      const rect = panel.getBoundingClientRect();
      const clampAxis = (value: number, size: number, extent: number) =>
        size >= extent - PADDING * 2
          ? PADDING
          : Math.min(Math.max(PADDING, value), extent - size - PADDING);
      const next = {
        x: clampAxis(rawX, rect.width, viewWidth),
        y: clampAxis(rawY, rect.height, viewHeight),
        arrowX: middlewareData.arrow?.x ?? undefined,
        arrowY: middlewareData.arrow?.y ?? undefined,
      };
      // animationFrame 重算下每帧都会走到这里：坐标没变就不 setState，
      // 否则 60fps 的重复渲染会把输入框的击键帧拖慢。
      setApplied((prev) => (prev === appliedPlacement ? prev : appliedPlacement));
      setCoords((prev) =>
        prev &&
        prev.x === next.x &&
        prev.y === next.y &&
        prev.arrowX === next.arrowX &&
        prev.arrowY === next.arrowY
          ? prev
          : next,
      );
    };
    void place();
    // autoUpdate 覆盖滚动 / resize / 目标自身变化，不需要我们轮询；
    // 惰性锚点（弹窗）无法用 observer 感知，加 animationFrame 逐帧跟随
    const cleanup = autoUpdate(reference, panel, () => void place(), {
      animationFrame: Boolean(getAnchor),
    });
    return () => {
      cancelled = true;
      cleanup();
    };
    // key 而不是 anchor：矩形对象每帧都是新的，用它会把这里变成死循环
  }, [key, placement, anchor, getAnchor]);

  return createPortal(
    <div
      ref={panelRef}
      role="region"
      aria-live="polite"
      data-tutorial-overlay="coach"
      data-degraded={degraded ? "true" : "false"}
      data-coach-placement={applied}
      className={`pointer-events-none fixed z-[70] w-[380px] max-w-[92vw] rounded-2xl border bg-card/97 p-4 text-left shadow-2xl backdrop-blur transition-[left,top] duration-150 motion-reduce:transition-none ${
        degraded ? "border-warning/45" : "border-primary/25"
      }`}
      style={
        coords
          ? { left: coords.x, top: coords.y }
          : { ...FALLBACK_CENTER_STYLE, opacity: anchor ? 0 : 1 }
      }
    >
      {children}
      <div
        ref={arrowRef}
        data-tutorial-arrow="true"
        style={{ left: coords?.arrowX, top: coords?.arrowY }}
        className="absolute h-2 w-2 rotate-45 border border-primary/25 bg-card/97"
      />
    </div>,
    document.body,
  );
}
