import { arrow, autoUpdate, computePosition, flip, offset, shift } from "@floating-ui/dom";
import type { ReactNode } from "react";
import { useLayoutEffect, useRef, useState } from "react";
import type { TutorialPlacement } from "../../api/tutorial";

/**
 * 教练面板：贴在真实目标旁边的说明卡片。
 *
 * 为什么不自己算 top/left：目标可能贴边、可能被 Tab 裁切、面板本身还要放
 * "为什么重要"的展开内容。Floating UI 的 flip/shift 会把它拉回可视区，
 * 而 autoUpdate 负责滚动与 resize 期间持续跟随 —— 这正是 §"窗口大小改变后
 * 遮罩位置仍然正确"要求的行为。
 */

const FALLBACK_CENTER_STYLE = {
  left: "50%",
  top: "50%",
  transform: "translate(-50%, -50%)",
} as const;

interface CoachPanelProps {
  /** 目标矩形；null 表示没有可依附的目标（引擎此时渲染兜底态） */
  anchor: DOMRect | null;
  placement: TutorialPlacement;
  children: ReactNode;
  /** 兜底态用更醒目的描边，让用户知道"教程没瞎指，是找不到东西" */
  degraded?: boolean;
}

function rectKey(rect: DOMRect | null): string {
  if (!rect) return "none";
  return `${Math.round(rect.left)},${Math.round(rect.top)},${Math.round(rect.width)}x${Math.round(rect.height)}`;
}

export function CoachPanel({ anchor, placement, children, degraded }: CoachPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const arrowRef = useRef<HTMLDivElement>(null);
  const [positioned, setPositioned] = useState(false);
  const key = rectKey(anchor);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel || !anchor) {
      setPositioned(false);
      return undefined;
    }
    const reference = { getBoundingClientRect: () => anchor };
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    const place = async () => {
      const {
        x,
        y,
        placement: applied,
        middlewareData,
      } = await computePosition(reference, panel, {
        placement: placement === "auto" ? "bottom" : placement,
        strategy: "fixed",
        middleware: [
          offset(14),
          flip({ padding: 12 }),
          shift({ padding: 12 }),
          // 面板本身是 w-[380px] max-w-[92vw]，窄屏由 CSS 兜住；
          // 这里只负责"不要压在屏幕上"，不再用 size 中间件改宽度。
          ...(arrowRef.current ? [arrow({ element: arrowRef.current })] : []),
        ],
      });
      if (cancelled) return;
      panel.style.left = `${x}px`;
      panel.style.top = `${y}px`;
      panel.dataset.coachPlacement = applied;
      const arrowData = middlewareData.arrow;
      if (arrowRef.current && arrowData) {
        arrowRef.current.style.left = arrowData.x != null ? `${arrowData.x}px` : "";
        arrowRef.current.style.top = arrowData.y != null ? `${arrowData.y}px` : "";
      }
      setPositioned(true);
    };
    void place();
    // autoUpdate 覆盖滚动/resize/目标自身变化，不需要我们轮询
    if (typeof window !== "undefined") {
      cleanup = autoUpdate(reference, panel, () => void place());
    }
    return () => {
      cancelled = true;
      cleanup?.();
    };
    // key 而不是 anchor：矩形对象每次测量都是新的，用它会死循环重渲染
  }, [key, placement, anchor]);

  const centered = !anchor || !positioned;

  return (
    <div
      ref={panelRef}
      role="region"
      aria-live="polite"
      data-tutorial-overlay="coach"
      data-degraded={degraded ? "true" : "false"}
      className={`fixed z-[70] w-[380px] max-w-[92vw] rounded-2xl border bg-card/97 p-4 text-left shadow-2xl backdrop-blur transition-[left,top] duration-150 motion-reduce:transition-none ${
        degraded ? "border-warning/45" : "border-primary/25"
      } ${centered ? "opacity-0" : "opacity-100"}`}
      style={centered ? { ...FALLBACK_CENTER_STYLE, opacity: anchor ? 0 : 1 } : undefined}
    >
      {children}
      <div
        ref={arrowRef}
        data-tutorial-arrow="true"
        className="absolute h-2 w-2 rotate-45 border border-primary/25 bg-card/97"
      />
    </div>
  );
}
