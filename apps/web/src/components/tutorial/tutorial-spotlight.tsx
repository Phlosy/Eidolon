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
 */

const PADDING = 8;

interface SpotlightProps {
  snapshot: TutorialTargetSnapshot;
  interactionMode: TutorialInteractionMode;
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

export function TutorialSpotlight({ snapshot, interactionMode }: SpotlightProps) {
  if (interactionMode === "NON_BLOCKING") return null; // 不挡操作，也不打暗
  if (snapshot.status !== "visible" || !snapshot.rect) return null;

  const { left, top, width, height } = snapshot.rect;
  const x = Math.max(0, left - PADDING);
  const y = Math.max(0, top - PADDING);
  const w = width + PADDING * 2;
  const h = height + PADDING * 2;
  const blocking = interactionMode === "TARGET_ONLY";

  return (
    <div
      data-tutorial-overlay="spotlight"
      data-tutorial-blocking={blocking ? "true" : "false"}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-[60] overflow-hidden"
    >
      {box({ left: 0, top: 0, right: 0, height: y }, "transition-[height]", blocking)}
      {box(
        { left: 0, top: y, width: x, height: h },
        "transition-[left,width,height,top]",
        blocking,
      )}
      {box({ left: x + w, top: y, right: 0, height: h }, "transition-[top,height,right]", blocking)}
      {box(
        { left: 0, top: y + h, right: 0, bottom: 0 },
        "transition-[top,bottom,left,right]",
        blocking,
      )}
      <div
        data-tutorial-halo="true"
        className="absolute animate-pulse rounded-xl ring-2 ring-primary/80 ring-offset-2 ring-offset-black/40 motion-reduce:animate-none"
        style={{ left: x, top: y, width: w, height: h }}
      />
    </div>
  );
}
