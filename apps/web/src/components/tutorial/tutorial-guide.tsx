import {
  ArrowLeft,
  ArrowRight,
  Check,
  Circle,
  GraduationCap,
  Pause,
  Play,
  SkipForward,
  X,
} from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useCompleteTutorialStep,
  usePauseTutorial,
  useResumeTutorial,
  useSkipTutorial,
  useSkipTutorialStep,
  useTutorial,
  useTutorialDefinition,
} from "../../hooks/useTutorial";
import { Button } from "../common/button";
import { cn } from "../../utils/cn";

export function TutorialGuide() {
  const { t, i18n } = useTranslation("auth");
  const location = useLocation();
  const tutorial = useTutorial().data;
  const definition = useTutorialDefinition().data;
  const skip = useSkipTutorial();
  const pause = usePauseTutorial();
  const resume = useResumeTutorial();
  const complete = useCompleteTutorialStep();
  const skipStep = useSkipTutorialStep();
  if (
    !tutorial ||
    tutorial.status === "not_started" ||
    tutorial.status === "completed" ||
    tutorial.status === "skipped" ||
    location.pathname.includes("/reviews/")
  )
    return null;
  if (tutorial.status === "paused")
    return (
      <aside
        className="fixed bottom-4 right-4 z-30 flex items-center gap-3 rounded-2xl border border-primary/25 bg-card/96 p-3 shadow-2xl backdrop-blur"
        aria-label={t("tutorial.paused")}
      >
        <GraduationCap className="h-4 w-4 text-primary" />
        <span className="text-xs">{t("tutorial.paused")}</span>
        <Button size="sm" onClick={() => resume.mutate()}>
          <Play className="h-3.5 w-3.5" />
          {t("tutorial.resume")}
        </Button>
      </aside>
    );
  if (!definition) return null;
  const steps = definition.stages.flatMap((stage) =>
    stage.steps.map((step) => ({ ...step, stage: stage.id, stageTitle: stage.title })),
  );
  const currentIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === tutorial.current_step),
  );
  const current = steps[currentIndex];
  const previous = steps[currentIndex - 1];
  const locale = i18n.language.startsWith("en") ? "en-US" : "zh-CN";

  return (
    <aside
      className="fixed inset-x-3 bottom-3 z-30 max-h-[76dvh] overflow-y-auto rounded-2xl border border-primary/25 bg-card/96 p-4 shadow-2xl backdrop-blur md:inset-x-auto md:bottom-5 md:right-5 md:w-[380px]"
      aria-label={t("tutorial.kicker")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <GraduationCap className="h-4 w-4" />
          </span>
          <div>
            <p className="type-kicker text-primary">{t("tutorial.kicker")}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {currentIndex + 1} / {steps.length} · {current.stageTitle[locale]}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("tutorial.skip")}
          onClick={() => {
            if (window.confirm(t("tutorial.skipConfirm"))) skip.mutate();
          }}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ol
        className="mt-4 grid gap-1"
        style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}
        aria-label={t("tutorial.progress")}
      >
        {steps.map((step) => {
          const done =
            tutorial.completed_steps.includes(step.id) || tutorial.skipped_steps.includes(step.id);
          const active = step.id === tutorial.current_step;
          return (
            <li
              key={step.id}
              title={t(`tutorial.steps.${step.id}.label`)}
              className={cn(
                "flex h-1.5 rounded-full bg-muted",
                done && "bg-success",
                active && "bg-primary",
              )}
            >
              <span className="sr-only">
                {t(`tutorial.steps.${step.id}.label`)}:{" "}
                {done
                  ? t("tutorial.completed")
                  : active
                    ? t("tutorial.current")
                    : t("tutorial.pending")}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="mt-5">
        <div className="flex items-center gap-2">
          {tutorial.completed_steps.includes(current.id) ? (
            <Check className="h-4 w-4 text-success" />
          ) : (
            <Circle className="h-4 w-4 text-primary" />
          )}
          <h2 className="text-base font-semibold">{t(`tutorial.steps.${current.id}.label`)}</h2>
        </div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          {t(`tutorial.steps.${current.id}.explanation`)}
        </p>
        <p className="mt-3 rounded-lg border border-border bg-background/45 px-3 py-2 font-mono text-[10px] text-muted-foreground">
          {t("tutorial.requirement")} · {current.requirement}
        </p>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {previous ? (
          <Link
            to={previous.route}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-border px-3 text-xs hover:bg-muted"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("tutorial.previous")}
          </Link>
        ) : null}
        <Link
          to={current.route}
          className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-xs font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("tutorial.go")}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        {current.id === "company_setup" ? (
          <Button size="sm" variant="outline" onClick={() => complete.mutate(current.id)}>
            {t("tutorial.understood")}
          </Button>
        ) : null}
        {current.optional ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => skipStep.mutate(current.id)}
          >
            <SkipForward className="h-3.5 w-3.5" />
            {t("tutorial.later")}
          </Button>
        ) : null}
      </div>
      <button
        type="button"
        className="mt-3 flex min-h-9 w-full items-center justify-center gap-2 rounded-lg text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={() => pause.mutate()}
      >
        <Pause className="h-3.5 w-3.5" />
        {t("tutorial.pause")}
      </button>
    </aside>
  );
}
