import { useState } from "react";
import { GraduationCap, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { useStartPractice, useTutorialLibrary } from "../../hooks/useTutorial";
import { Button } from "../common/button";
import { PracticeDialog } from "./practice-dialog";

/** 用户关掉交接卡片后不再打扰；开始实战后卡片本来就会因状态变化消失。 */
const DISMISS_KEY = "eidolon.tutorial.practiceHandoff.dismissed";
const CORE_TUTORIAL = "company-founding";
/** 与聚光灯引擎保持一致：全屏页面不叠加引导。 */
const EXCLUDED_ROUTES: RegExp[] = [/^\/office$/, /\/reviews\//];

/**
 * 核心教程通关后的交接卡片。
 *
 * 核心教程到「第一位工程师能干活」就结束了，项目实战是另一条独立进度，
 * 而且必须先看成本确认才能开始 —— 所以它不会自动接上。没有这张卡片，
 * 聚光灯消失后用户只会觉得"教程莫名其妙就没了"。
 */
export function TutorialHandoff() {
  const { t } = useTranslation("tutorial");
  const location = useLocation();
  const library = useTutorialLibrary();
  const startPractice = useStartPractice();
  const [dismissed, setDismissed] = useState(
    () => window.localStorage.getItem(DISMISS_KEY) === "1",
  );
  const [dialogOpen, setDialogOpen] = useState(false);

  if (EXCLUDED_ROUTES.some((pattern) => pattern.test(location.pathname))) return null;
  const items = library.data?.tutorials ?? [];
  const core = items.find((item) => item.definition.id === CORE_TUTORIAL);
  const practice = items.find((item) => item.definition.id !== CORE_TUTORIAL);
  if (!core || !practice) return null;
  if (core.progress.status !== "completed") return null;
  const practiceStatus = practice.progress.status;
  if (practiceStatus !== "not_started" && practiceStatus !== "skipped") return null;
  if (dismissed) return null;

  const dismiss = () => {
    window.localStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  };

  return (
    <>
      <aside
        data-tutorial-handoff="true"
        className="fixed bottom-4 right-4 z-[60] w-[360px] max-w-[92vw] rounded-2xl border border-primary/25 bg-card/97 p-4 shadow-2xl backdrop-blur"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <GraduationCap className="h-4 w-4" />
            </span>
            <p className="text-sm font-semibold">{t("handoff.title")}</p>
          </div>
          <button
            type="button"
            aria-label={t("handoff.dismiss")}
            onClick={dismiss}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">{t("handoff.body")}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            size="sm"
            data-tutorial-action="handoff-start"
            onClick={() => setDialogOpen(true)}
          >
            {t("handoff.start")}
          </Button>
          <Button size="sm" variant="ghost" onClick={dismiss}>
            {t("handoff.later")}
          </Button>
        </div>
      </aside>
      <PracticeDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onStart={() => startPractice.mutate()}
      />
    </>
  );
}
