import { createPortal } from "react-dom";
import type { CSSProperties } from "react";
import type { TutorialInteractionMode } from "../../api/tutorial";
import type { TutorialTargetSnapshot } from "./target-registry";

/**
 * Interactive Spotlight 的遮罩层。
 *
 * 用"四个矩形"拼出挖孔，而不是 SVG mask：
 * - 四块矩形天然就是 TARGET_ONLY 的点击拦截层（孔上没有任何元素，点击直接
 *   落到真实目标上，不需要"假装放行"的 hack）；
 * - 坐标只依赖 getBoundingClientRect，缩放/滚动重算代价低。
 *
 * NON_BLOCKING 不铺暗（用户要在页面上继续操作），但仍然画出光环标记目标。
 */

const PADDING = 8;

interface SpotlightProps {
  snapshot: TutorialTargetSnapshot;
  interactionMode: TutorialInteractionMode;
  /** 提醒模式：用户没操作就点了下一步 → 光环换成醒目提醒色。 */
  pulse?: boolean;
}

function box(style: CSSProperties, className: string, blocking: boolean) {
  return (
    <div
      data-tutorial-dim="true"
      className={`absolute bg-black/65 backdrop-blur-[1px] ${className}`}
      style={{ ...style, pointerEvents: blocking ? "auto" : "none" }}
    />
  );
}

export function TutorialSpotlight({ snapshot, interactionMode, pulse }: SpotlightProps) {
  if (snapshot.status !== "visible" || !snapshot.rect) return null;

  const { left, top, width, height } = snapshot.rect;
  const x = Math.max(0, left - PADDING);
  const y = Math.max(0, top - PADDING);
  const w = width + PADDING * 2;
  const h = height + PADDING * 2;
  const blocking = interactionMode === "TARGET_ONLY";
  // NON_BLOCKING 的步骤（例如"去运行时标签页里填表单"）用户要在页面上到处点，
  // 铺暗反而碍事；但光环必须有 —— 否则只剩一块悬空面板，用户不知道点哪儿。
  const dimmed = interactionMode !== "NON_BLOCKING";

  // 与 CoachPanel 同理：挖孔层也要以视口为包含块，portal 到 body
  return createPortal(
    <div
      data-tutorial-overlay="spotlight"
      data-tutorial-blocking={blocking ? "true" : "false"}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-[60] overflow-hidden"
    >
      {dimmed ? (
        <>
          {box({ left: 0, top: 0, right: 0, height: y }, "transition-[height]", blocking)}
          {box(
            { left: 0, top: y, width: x, height: h },
            "transition-[left,width,height,top]",
            blocking,
          )}
          {box(
            { left: x + w, top: y, right: 0, height: h },
            "transition-[top,height,right]",
            blocking,
          )}
          {box(
            { left: 0, top: y + h, right: 0, bottom: 0 },
            "transition-[top,bottom,left,right]",
            blocking,
          )}
        </>
      ) : null}
      <div
        data-tutorial-halo="true"
        data-tutorial-halo-pulse={pulse ? "true" : "false"}
        className={
          pulse
            ? "absolute animate-pulse rounded-xl ring-[3px] ring-danger ring-offset-2 ring-offset-black/40 motion-reduce:animate-none"
            : "absolute animate-pulse rounded-xl ring-2 ring-primary/80 ring-offset-2 ring-offset-black/40 motion-reduce:animate-none"
        }
        style={{ left: x, top: y, width: w, height: h }}
        aria-hidden="true"
      />
    </div>,
    document.body,
  );
}
