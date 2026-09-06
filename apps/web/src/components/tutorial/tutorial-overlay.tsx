import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleDot,
  GraduationCap,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { cn } from "../../utils/cn";
import { CoachPanel } from "./coach-panel";
import { TutorialSpotlight } from "./tutorial-spotlight";
import { useTutorialEngine } from "./use-tutorial-engine";
import { tutorialTargets } from "./target-registry";
import { stopReplay } from "./tutorial-replay";

/**
 * 互动聚光灯教程的唯一出口组件：挂在 AppShell 上，自己决定
 * 打光在哪儿、面板说什么、按哪种 interaction_mode 拦点击。
 */

function ProgressDots({ total, index, done }: { total: number; index: number; done: boolean }) {
  return (
    <ol className="mt-3 flex gap-1" aria-label="progress">
      {Array.from({ length: total }).map((_, position) => (
        <li
          key={position}
          className={cn(
            "h-1.5 flex-1 rounded-full bg-muted",
            position < index && "bg-success",
            position === index && (done ? "bg-success" : "bg-primary"),
          )}
        />
      ))}
    </ol>
  );
}

function WhySection({ stepId }: { stepId: string }) {
  const { t } = useTranslation("tutorial");
  const [open, setOpen] = useState(false);
  const body = t(`steps.${stepId}.why`);
  if (body === `steps.${stepId}.why`) return null; // 没配文案就不显示空壳
  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-8 items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        {t("ui.whyTitle")}
      </button>
      {open ? (
        <p data-tutorial-why="true" className="mt-1 text-[11px] leading-5 text-muted-foreground">
          {body}
        </p>
      ) : null}
    </div>
  );
}

export function TutorialOverlay() {
  const { t } = useTranslation("tutorial");
  const engine = useTutorialEngine();
  // 弹窗锚点（向导无锚点段落用）不经 Target Registry，弹窗开着时 resize/scroll
  // 自己催一次重渲染来重读弹窗位置
  const [, setDialogBump] = useState(0);
  useEffect(() => {
    const bump = () => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        setDialogBump((n) => n + 1);
      }
    };
    window.addEventListener("resize", bump);
    window.addEventListener("scroll", bump, true);
    return () => {
      window.removeEventListener("resize", bump);
      window.removeEventListener("scroll", bump, true);
    };
  }, []);
  const { step } = engine;
  if (!step) return null;

  const paused = engine.progress?.status === "paused";
  if (paused) {
    // 暂停时完全不遮挡页面，只留一个入口（§"暂停不遮挡，但保留继续入口"）
    return (
      <aside
        data-tutorial-overlay="paused"
        className="fixed bottom-4 right-4 z-[60] flex items-center gap-3 rounded-2xl border border-primary/25 bg-card/96 p-3 shadow-2xl backdrop-blur"
      >
        <GraduationCap className="h-4 w-4 text-primary" />
        <span className="text-xs">{t("ui.pausedTitle")}</span>
        <button
          type="button"
          onClick={engine.togglePause}
          className="inline-flex h-8 items-center gap-1.5 rounded-xl bg-primary px-3 text-xs font-medium text-primary-foreground"
        >
          <Play className="h-3.5 w-3.5" />
          {t("ui.resume")}
        </button>
      </aside>
    );
  }

  const replay = engine.mode === "replay";
  const cannotRoute = !replay && engine.pendingParams.length > 0;
  // 兜底态只在"已经在对的页面上、却抓不到目标"时出现；还没导航过去不算兜底
  const hinting = Boolean(engine.hint);
  // 向导指引态下，"目标在弹窗外"不是错误：那时我们打的就是向导内部的控件
  const degraded =
    !hinting &&
    (cannotRoute || (!replay && engine.onRoute && engine.snapshot.status !== "visible"));
  // 信息步没有"要点哪个控件"：面板居中、整页压暗即可，不给整面墙画光环
  const isInfo = step.kind === "INFORMATION";
  // 指引锚点当前可见才算"钉住"；向导翻页会卸载上一段 DOM，翻页间隙锚点可能缺失
  const hintAnchored = hinting && engine.snapshot.status === "visible";
  // 向导走到没有锚点的段落（部门、汇报线等）：面板贴弹窗右侧，别退到屏幕正中挡操作
  const dialogRect =
    hinting && !hintAnchored
      ? (document
          .querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]')
          ?.getBoundingClientRect() ?? null)
      : null;
  const anchor: DOMRect | null =
    !isInfo && !replay && engine.onRoute && engine.snapshot.status === "visible"
      ? engine.snapshot.rect
      : dialogRect;

  const fallbackCopy = cannotRoute
    ? { title: "ui.routePendingTitle", body: "ui.routePendingBody" }
    : engine.snapshot.status === "hidden"
      ? { title: "ui.targetHiddenTitle", body: "ui.targetHiddenBody" }
      : engine.snapshot.status === "covered"
        ? { title: "ui.targetCoveredTitle", body: "ui.targetCoveredBody" }
        : { title: "ui.targetMissingTitle", body: "ui.targetMissingBody" };

  return (
    <>
      {!replay ? (
        isInfo ? (
          // 信息步没有页面上的点击目标：整页压暗，让居中的面板成为唯一焦点
          <div
            data-tutorial-overlay="dim"
            aria-hidden="true"
            className="pointer-events-none fixed inset-0 z-[60] bg-black/65 backdrop-blur-[1px]"
          />
        ) : (
          <TutorialSpotlight
            snapshot={engine.snapshot}
            // 指引态强制降级为非拦截：TARGET_ONLY 的遮罩会把向导自己的"下一步"
            // 一起吃掉，用户就被教程锁死在弹窗里（实测过）。
            interactionMode={hinting ? "FOCUS_ONLY" : step.interaction_mode}
          />
        )
      ) : null}
      {/* 指引态换成侧边摆放：这一步的 placement 是给"页面上的目标"定的（例如 top），
          而指引目标是弹窗/标签页里的控件，沿用同一个方位会让面板正好压住用户
          下一个要点开的元素（Playwright 实测因此点不动"运行时"标签页）。
          真被挡住时用户还能按暂停收成小药丸，但默认就不该挡。 */}
      <CoachPanel
        anchor={anchor}
        placement={hinting ? "right" : step.placement}
        degraded={degraded}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <GraduationCap className="h-4 w-4" />
            </span>
            <div>
              <p className="type-kicker text-primary">
                {replay ? t("ui.replay.badge") : t("ui.kicker")}
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {t("ui.stepCounter", { current: engine.index + 1, total: engine.total })}
              </p>
            </div>
          </div>
          <button
            type="button"
            aria-label={replay ? t("ui.replay.exit") : t(engine.exitLabelKey)}
            onClick={replay ? () => stopReplay() : engine.togglePause}
            className="flex h-8 w-8 items-center justify-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {replay ? <X className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
          </button>
        </div>

        <ProgressDots total={engine.total} index={engine.index} done={engine.stepDone} />

        <div className="mt-4">
          <div className="flex items-center gap-2">
            {engine.stepDone ? (
              <Check className="h-4 w-4 shrink-0 text-success" />
            ) : (
              <CircleDot className="h-4 w-4 shrink-0 text-primary" />
            )}
            <h2 className="text-sm font-semibold">{t(`steps.${step.id}.label`)}</h2>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {t(`steps.${step.id}.explanation`)}
          </p>
          {step.has_why ? <WhySection stepId={step.id} /> : null}
        </div>

        {hintAnchored && engine.hint ? (
          <div
            data-tutorial-hint="true"
            className="mt-3 rounded-xl border border-primary/25 bg-primary/5 p-3"
          >
            <p className="text-xs leading-5">{t(engine.hint.textKey)}</p>
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={engine.previousHint}
                disabled={engine.hintIndex <= 0}
                className="inline-flex h-8 items-center rounded-lg border border-border px-2.5 text-[11px] disabled:opacity-40"
              >
                {t("ui.previous")}
              </button>
              <button
                type="button"
                data-tutorial-action="next-hint"
                onClick={engine.nextHint}
                disabled={engine.hintIndex >= engine.hints.length - 1}
                className="inline-flex h-8 items-center rounded-lg border border-border px-2.5 text-[11px] disabled:opacity-40"
              >
                {t("ui.next")}
              </button>
            </div>
          </div>
        ) : null}

        {degraded ? (
          <div
            data-tutorial-fallback="true"
            className="mt-3 rounded-xl border border-warning/40 bg-warning/5 p-3"
          >
            <p className="flex items-center gap-1.5 text-xs font-medium text-warning">
              <AlertTriangle className="h-3.5 w-3.5" />
              {t(fallbackCopy.title)}
            </p>
            <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
              {t(fallbackCopy.body)}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={engine.goToTarget}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[11px] hover:bg-muted"
              >
                {t("ui.manualOpen")}
              </button>
              <button
                type="button"
                onClick={() => tutorialTargets.refresh()}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[11px] hover:bg-muted"
              >
                <RotateCcw className="h-3 w-3" />
                {t("ui.retryLocate")}
              </button>
            </div>
          </div>
        ) : null}

        {replay ? (
          <p className="mt-3 text-[11px] leading-5 text-muted-foreground">{t("ui.replay.note")}</p>
        ) : null}

        {engine.errorMessage ? (
          <p
            data-tutorial-error="true"
            className="mt-2 flex items-start justify-between gap-2 text-[11px] text-danger"
          >
            <span>{engine.errorMessage}</span>
            <button type="button" onClick={engine.dismissError} aria-label={t("ui.dismiss")}>
              <X className="h-3.5 w-3.5" />
            </button>
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {replay ? (
            <>
              <button
                type="button"
                onClick={engine.replayPrevious}
                disabled={engine.index === 0}
                className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-border px-3 text-xs disabled:opacity-40"
              >
                {t("ui.replay.prev")}
              </button>
              <button
                type="button"
                onClick={engine.replayNext}
                disabled={engine.index >= engine.total - 1}
                className="inline-flex h-9 flex-1 items-center justify-center rounded-xl bg-primary px-4 text-xs font-medium text-primary-foreground disabled:opacity-40"
              >
                {t("ui.replay.next")}
              </button>
            </>
          ) : (
            <>
              {step.kind === "INFORMATION" && !engine.stepDone ? (
                <button
                  type="button"
                  onClick={engine.acknowledge}
                  disabled={engine.busy}
                  className="inline-flex h-9 flex-1 items-center justify-center rounded-xl bg-primary px-4 text-xs font-medium text-primary-foreground disabled:opacity-60"
                >
                  {t("ui.gotIt")}
                </button>
              ) : hinting ? null : !engine.onRoute ? (
                <button
                  type="button"
                  onClick={engine.goToTarget}
                  className="inline-flex h-9 flex-1 items-center justify-center rounded-xl bg-primary px-4 text-xs font-medium text-primary-foreground"
                >
                  {t("ui.openPage")}
                </button>
              ) : (
                <p className="flex-1 text-[11px] leading-5 text-muted-foreground">
                  {t("ui.autoAdvanceHint")}
                </p>
              )}
              {step.allow_skip && step.kind !== "INFORMATION" ? (
                <button
                  type="button"
                  onClick={engine.skipStep}
                  disabled={engine.busy}
                  className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-border px-3 text-xs hover:bg-muted"
                >
                  <SkipForward className="h-3.5 w-3.5" />
                  {t("ui.later")}
                </button>
              ) : null}
            </>
          )}
        </div>

        {!replay ? (
          <Link
            to="/settings/tutorial"
            className="mt-2 inline-block text-[10px] text-muted-foreground underline-offset-2 hover:underline"
          >
            {t("ui.exit")}
          </Link>
        ) : null}
      </CoachPanel>
    </>
  );
}
