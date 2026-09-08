/**
 * Tutorial Target Registry —— 教程"往哪儿打光"的唯一入口。
 *
 * 存在的理由：页面是 React 渲染出来的，DOM 随时会被换掉（列表重绘、Tab 切换、
 * Dialog 走 Portal）。如果教程引擎在挂载时算一次坐标，聚光灯一定会指错。
 * 所以这里维护订阅，靠 MutationObserver + ResizeObserver + scroll/resize 事件
 * 重新测量，并用一帧（rAF）合批，避免每个 DOM 变化都强制同步布局。
 */

export type TutorialTargetStatus = "visible" | "hidden" | "missing" | "covered";

export interface TutorialTargetQuery {
  /** 步骤声明里的 target_id */
  id: string;
  /** 同页多个同名 target 时的区分键，例如 data-tutorial-key="engineer" */
  key?: string | null;
}

export interface TutorialTargetSnapshot {
  status: TutorialTargetStatus;
  element: HTMLElement | null;
  rect: DOMRect | null;
  /** 用户最近在聚光灯目标上操作过（点击/输入）—— "操作过就不必再点下一步"。 */
  engaged: boolean;
}

export const MISSING_SNAPSHOT: TutorialTargetSnapshot = {
  status: "missing",
  element: null,
  rect: null,
  engaged: false,
};

/** 迁移期同时认旧的 data-tutorial，避免"改了属性名的页面"教程直接瞎掉。 */
const TARGET_SELECTOR = "[data-tutorial-target], [data-tutorial]";
const MODAL_SELECTOR = '[role="dialog"][aria-modal="true"]';

function attrMatches(element: HTMLElement, id: string): boolean {
  return (
    element.getAttribute("data-tutorial-target") === id ||
    element.getAttribute("data-tutorial") === id
  );
}

/**
 * 可见性自己算，不用 checkVisibility()：jsdom 没有它，而这个判断的语义
 * （教程不能把光打在一个 display:none 的节点上）必须能在测试里被钉住。
 */
export function isElementVisible(element: HTMLElement): boolean {
  let node: HTMLElement | null = element;
  while (node) {
    if (node.hasAttribute("hidden")) return false;
    // disabled 只在表单控件上存在，类型上得绕一下
    if ((node as unknown as { disabled?: boolean }).disabled === true) return false;
    const style = window.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    node = node.parentElement;
  }
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

/**
 * 选择策略（写死在这里，别散到组件里）：
 * 1. 有 key 就只认 key 命中的节点 —— 指定了 key 却没命中时返回 null，
 *    宁可让引擎走兜底面板，也不能把光打在"另一个员工"身上；
 * 2. 可见的优先，但不可见的也照样返回：由 readTargetSnapshot 区分
 *    "hidden"（入口存在但被折叠）与 "missing"（根本没有）。
 *    如果把不可见节点在这里就滤掉，用户看到的会是"找不到元素"，
 *    而真实原因是"该展开还没展开"——那是两种完全不同的指引。
 */
export function findTargetElement(query: TutorialTargetQuery): HTMLElement | null {
  const nodes = Array.from(document.querySelectorAll<HTMLElement>(TARGET_SELECTOR)).filter(
    (element) => attrMatches(element, query.id),
  );
  if (!nodes.length) return null;
  const pool = query.key
    ? nodes.filter((element) => element.getAttribute("data-tutorial-key") === query.key)
    : nodes;
  if (!pool.length) return null;
  return pool.find(isElementVisible) ?? pool[0];
}

function openModal(): HTMLElement | null {
  return document.querySelector<HTMLElement>(MODAL_SELECTOR);
}

export function readTargetSnapshot(query: TutorialTargetQuery): TutorialTargetSnapshot {
  const element = findTargetElement(query);
  if (!element) return MISSING_SNAPSHOT;
  if (!isElementVisible(element)) {
    return { status: "hidden", element, rect: null, engaged: false };
  }
  // 目标存在但落在当前打开的弹窗之外：先让用户处理弹窗，否则聚光灯会打在
  // 一个他根本点不到的地方（§"目标被 Modal 遮挡时给出明确提示"）。
  const modal = openModal();
  if (modal && !modal.contains(element)) {
    return {
      status: "covered",
      element,
      rect: element.getBoundingClientRect(),
      engaged: false,
    };
  }
  return {
    status: "visible",
    element,
    rect: element.getBoundingClientRect(),
    engaged: false,
  };
}

function snapshotKey(snapshot: TutorialTargetSnapshot): string {
  if (!snapshot.rect) return `${snapshot.status}:none:${snapshot.engaged ? 1 : 0}`;
  const { x, y, width, height } = snapshot.rect;
  // 亚像素抖动会让 rAF 每一帧都"看起来变了"，这里量化到整像素
  return `${snapshot.status}:${Math.round(x)},${Math.round(y)},${Math.round(width)}x${Math.round(height)}:${snapshot.engaged ? 1 : 0}`;
}

type Listener = (snapshot: TutorialTargetSnapshot) => void;

interface ListenerEntry {
  onSnapshot: Listener;
  onEngage?: () => void;
}

class TutorialTargetRegistry {
  private listeners = new Map<string, Set<ListenerEntry>>();
  private latest = new Map<string, string>();
  private observer: MutationObserver | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private tracked = new Map<HTMLElement, Set<string>>();
  private frame: number | null = null;
  private started = false;
  /** 目标被用户操作过的查询键集合（"操作过就不用重教"）。 */
  private engagedKeys = new Set<string>();

  subscribe(query: TutorialTargetQuery, listener: Listener, onEngage?: () => void): () => void {
    const key = this.keyOf(query);
    let set = this.listeners.get(key);
    if (!set) {
      set = new Set();
      this.listeners.set(key, set);
    }
    const entry: ListenerEntry = { onSnapshot: listener, onEngage };
    set.add(entry);
    this.ensureObservers();
    // 立刻给一次当前值，React 首帧不至于空白
    listener(readTargetSnapshot(query));
    return () => {
      set.delete(entry);
      if (!set.size) {
        this.listeners.delete(key);
        this.latest.delete(key);
      }
    };
  }

  isEngaged(query: TutorialTargetQuery): boolean {
    return this.engagedKeys.has(this.keyOf(query));
  }

  /** 供测试与"刚点完按钮 DOM 才变"的场景手动催一次测量。 */
  refresh(): void {
    this.schedule();
  }

  private keyOf(query: TutorialTargetQuery): string {
    return `${query.id}\u0000${query.key ?? ""}`;
  }

  private ensureObservers(): void {
    if (this.started || typeof document === "undefined") return;
    this.started = true;
    if (typeof MutationObserver !== "undefined") {
      this.observer = new MutationObserver(() => this.schedule());
      this.observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: [
          "data-tutorial-target",
          "data-tutorial",
          "data-tutorial-key",
          "class",
          "style",
          "hidden",
          "disabled",
          "aria-hidden",
          "open",
        ],
      });
    }
    // 用户在聚光灯目标上的任何操作都算"学过这一步"：点过聚光灯照的区域，
    // 教学卡片就不该再要求重复下一步；操作卡自己的下一步/确定也算操作。
    for (const eventName of ["click", "pointerdown", "change"] as const) {
      document.addEventListener(eventName, this.onEngageInteraction, { capture: true });
    }
    document.addEventListener("keydown", this.onEngageInteraction, { capture: true });
    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(() => this.schedule());
    }
    window.addEventListener("resize", this.onViewportChange, { passive: true });
    // capture: true —— 滚动可能发生在任意滚动容器上，不只 window
    window.addEventListener("scroll", this.onViewportChange, { capture: true, passive: true });
    document.addEventListener("transitionend", this.onViewportChange);
    document.addEventListener("animationend", this.onViewportChange);
  }

  private onViewportChange = () => this.schedule();

  private onEngageInteraction = (event: Event): void => {
    const target = event.target as HTMLElement | null;
    if (event.type === "keydown" && !["Enter", " "].includes((event as KeyboardEvent).key)) {
      return;
    }
    const hit = target?.closest<HTMLElement>(TARGET_SELECTOR);
    const id = hit?.getAttribute("data-tutorial-target") ?? hit?.getAttribute("data-tutorial");
    if (!id || !hit) return;
    for (const key of this.listeners.keys()) {
      const [queryId] = key.split("\u0000");
      if (queryId !== id) continue;
      const fresh = !this.engagedKeys.has(key);
      if (fresh) {
        this.engagedKeys.add(key);
        this.schedule();
      }
      const entries = this.listeners.get(key);
      if (fresh && entries) {
        for (const entry of entries) entry.onEngage?.();
      }
    }
  };

  private schedule(): void {
    if (this.frame !== null || this.listeners.size === 0) return;
    if (typeof requestAnimationFrame === "undefined") {
      this.run();
      return;
    }
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.run();
    });
  }

  private run(): void {
    for (const [key, entries] of this.listeners) {
      const [id, rawKey] = key.split("\u0000");
      const query: TutorialTargetQuery = { id, key: rawKey || null };
      const snapshot = readTargetSnapshot(query);
      snapshot.engaged = this.engagedKeys.has(key);
      const marker = snapshotKey(snapshot);
      if (this.latest.get(key) === marker) continue;
      this.latest.set(key, marker);
      this.retrack(snapshot.element);
      for (const entry of entries) entry.onSnapshot(snapshot);
    }
  }

  private retrack(element: HTMLElement | null): void {
    if (!this.resizeObserver) return;
    const wanted = new Set<HTMLElement>();
    if (element) wanted.add(element);
    for (const tracked of this.tracked.keys()) {
      if (!wanted.has(tracked)) {
        this.resizeObserver.unobserve(tracked);
        this.tracked.delete(tracked);
      }
    }
    for (const element of wanted) {
      if (!this.tracked.has(element)) {
        this.resizeObserver.observe(element);
        this.tracked.set(element, new Set());
      }
    }
  }
}

export const tutorialTargets = new TutorialTargetRegistry();
