import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  BookOpen,
  CircleHelp,
  Play,
  RotateCcw,
  ShieldAlert,
  SkipForward,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  flattenSteps,
  resolveRoute,
  type TutorialDefinition,
  type TutorialProgress,
} from "../../api/tutorial";
import {
  TUTORIAL_QUERY_KEY,
  useSkipPractice,
  useStartPractice,
  usePracticePreview,
  useTutorialLibrary,
} from "../../hooks/useTutorial";
import { startReplay } from "./tutorial-replay";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";

/**
 * 教程库 / 教程中心。
 *
 * 这里同时是"实战可延后"的入口：开始 Classic Snake 之前必须先看到真实成本
 * （哪些员工、哪个 runtime、哪个 provider/model），也允许零成本跳过 ——
 * 跳过与否由后端 /practice/skip 决定，前端只负责把它说清楚。
 */

const CORE_TUTORIAL = "company-founding";

interface TutorialCardProps {
  definition: TutorialDefinition;
  progress: TutorialProgress;
  onReplay: () => void;
  onSkipPractice: () => void;
  onStartPractice: () => void;
  continueTo: string;
}

function statusLabel(t: (key: string) => string, status: TutorialProgress["status"]): string {
  return {
    not_started: t("library.notStarted"),
    active: t("library.active"),
    paused: t("library.paused"),
    completed: t("library.completed"),
    skipped: t("library.skipped"),
  }[status];
}

function TutorialCard({
  definition,
  progress,
  onReplay,
  onSkipPractice,
  onStartPractice,
  continueTo,
}: TutorialCardProps) {
  const { t } = useTranslation("tutorial");
  const steps = flattenSteps(definition);
  const done = new Set([...progress.completed_steps, ...progress.skipped_steps]).size;
  const practice = definition.id !== CORE_TUTORIAL;
  return (
    <div
      data-tutorial-card={definition.id}
      className="rounded-2xl border border-border bg-background/35 p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="type-kicker text-primary">
            {practice ? t("library.practice") : t("library.core")}
          </p>
          <h3 className="mt-1 text-sm font-semibold">{t(definition.title_key)}</h3>
        </div>
        <span
          data-tutorial-card-status={progress.status}
          className="shrink-0 rounded-lg border border-border px-2 py-1 text-[10px] text-muted-foreground"
        >
          {statusLabel(t, progress.status)}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        {definition.description_key ? t(definition.description_key) : null}
      </p>
      <p className="mt-3 text-[10px] text-muted-foreground">
        {t("library.stepProgress", { done, total: steps.length })}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {practice ? (
          progress.status === "skipped" || progress.status === "not_started" ? (
            <Button size="sm" onClick={onStartPractice}>
              <Play className="h-3.5 w-3.5" />
              {t("library.resumePractice")}
            </Button>
          ) : null
        ) : progress.status === "active" || progress.status === "paused" ? (
          <Link
            to={continueTo}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-primary px-3 text-xs font-medium text-primary-foreground"
          >
            {t("library.continue")}
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        ) : null}
        {practice && progress.status !== "skipped" && progress.status !== "completed" ? (
          <Button size="sm" variant="outline" onClick={onSkipPractice}>
            <SkipForward className="h-3.5 w-3.5" />
            {t("library.skipPractice")}
          </Button>
        ) : null}
        {practice && progress.status === "skipped" ? (
          <span className="inline-flex items-center text-[10px] text-muted-foreground">
            {t("library.skippedWithCost")}
          </span>
        ) : null}
        <Button size="sm" variant="ghost" onClick={onReplay}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("library.replay")}
        </Button>
      </div>
    </div>
  );
}

function PracticeDialog({
  open,
  onOpenChange,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStart: () => void;
}) {
  const { t } = useTranslation("tutorial");
  const preview = usePracticePreview(open);
  const data = preview.data;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("practice.dialogTitle")}
      description={t("practice.dialogBody")}
    >
      <div className="space-y-4">
        <p className="rounded-xl border border-primary/25 bg-primary/5 px-3 py-2 text-[11px] text-primary">
          {t("practice.accelerated")}
        </p>
        {data && data.team.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("practice.noEmployees")}</p>
        ) : null}
        {data ? (
          <>
            <table className="w-full text-[11px]">
              <caption className="mb-1 text-left text-[10px] text-muted-foreground">
                {t("practice.team")}
              </caption>
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-normal">{t("practice.runtime")}</th>
                  <th className="py-1 text-left font-normal">{t("practice.provider")}</th>
                  <th className="py-1 text-left font-normal">{t("practice.model")}</th>
                </tr>
              </thead>
              <tbody>
                {data.team.map((member) => (
                  <tr key={member.employee_id} data-tutorial-team-row={member.employee_id}>
                    <td className="py-1 font-mono">
                      {member.name} · {member.runtime ?? "—"}
                    </td>
                    <td className="py-1 font-mono">{member.provider ?? "—"}</td>
                    <td className="py-1 font-mono">{member.model ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p
              data-tutorial-cost={data.uses_llm ? "real" : "mock"}
              className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-[11px] ${
                data.uses_llm
                  ? "border-danger/40 bg-danger/5 text-danger"
                  : "border-success/35 bg-success/5 text-success"
              }`}
            >
              {data.uses_llm ? <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : null}
              <span>{data.uses_llm ? t("practice.usesLlm") : t("practice.mockOnly")}</span>
            </p>
            {data.uses_llm ? (
              <div className="text-[10px] leading-5 text-muted-foreground">
                <p>{t("practice.mockOption")}</p>
                <p>{t("practice.mockOptionHint")}</p>
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-xs text-muted-foreground">…</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("practice.notNow")}
          </Button>
          <Button
            onClick={() => {
              onOpenChange(false);
              onStart();
            }}
            disabled={!data}
            data-tutorial-action="start-practice"
          >
            {t("practice.start")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export function TutorialCenter() {
  const { t } = useTranslation("tutorial");
  const queryClient = useQueryClient();
  const library = useTutorialLibrary();
  const skipPractice = useSkipPractice();
  const startPractice = useStartPractice();
  const [practiceOpen, setPracticeOpen] = useState(false);
  const items = library.data?.tutorials ?? [];
  const core = items.find((item) => item.definition.id === CORE_TUTORIAL);
  // "继续教程"要落到当前步骤真正的页面上：route 可能带 {占位符}，用 context 解析
  const coreContinueTo = (() => {
    if (!core) return "/";
    const step = flattenSteps(core.definition).find(
      (item) => item.id === core.progress.current_step,
    );
    return step ? resolveRoute(step.route, core.progress.context) : "/";
  })();
  const refresh = () => queryClient.invalidateQueries({ queryKey: TUTORIAL_QUERY_KEY });

  return (
    <section id="tutorial-center" className="command-panel p-5 md:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="type-kicker text-primary">{t("library.title")}</p>
          <h2 className="mt-2 flex items-center gap-2 text-xl font-semibold">
            <BookOpen className="h-5 w-5 text-primary" />
            {t("library.title")}
          </h2>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{t("library.description")}</p>
        </div>
        <CircleHelp className="h-5 w-5 text-muted-foreground" />
      </div>

      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        {items.map(({ definition, progress }) => (
          <TutorialCard
            key={definition.id}
            definition={definition}
            progress={progress}
            continueTo={progress.tutorial_id === CORE_TUTORIAL ? coreContinueTo : "/projects"}
            onReplay={() => startReplay(definition)}
            onStartPractice={() => setPracticeOpen(true)}
            onSkipPractice={() => skipPractice.mutate(undefined, { onSuccess: refresh })}
          />
        ))}
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {(library.data?.center ?? []).map((chapter) => (
          <Link
            key={chapter.id}
            to={chapter.route}
            className="group inline-flex min-h-9 items-center gap-1.5 rounded-xl border border-border bg-background/35 px-3 text-[11px] transition hover:border-border-active hover:bg-surface-interactive"
          >
            {t(chapter.title_key)}
            <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-primary" />
          </Link>
        ))}
      </div>

      <PracticeDialog
        open={practiceOpen}
        onOpenChange={setPracticeOpen}
        onStart={() => startPractice.mutate(undefined, { onSuccess: refresh })}
      />
    </section>
  );
}
