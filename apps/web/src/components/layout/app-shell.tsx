import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CompanySidebar } from "./company-sidebar";
import { TopCommandBar } from "./top-command-bar";
import { useEventStream } from "../../hooks/useEventStream";
import { TutorialOverlay } from "../tutorial/tutorial-overlay";
import { TutorialHandoff } from "../tutorial/tutorial-handoff";
import { cn } from "../../utils/cn";

/** 决策类页面（评审室）进入全屏聚焦：收起导航与 HUD，一屏只留决定。 */
const FOCUS_ROUTE = /^\/projects\/\d+\/reviews\/\d+/;

export function AppShell() {
  const { t } = useTranslation();
  const location = useLocation();
  const focusMode = FOCUS_ROUTE.test(location.pathname);
  // 默认收成图标轨道，把视觉重心让给中央舞台
  const [collapsed, setCollapsed] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  useEventStream();
  return (
    <div className="min-h-screen bg-background lg:flex">
      {mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label={t("nav:closeNavigation")}
        />
      ) : null}
      {!focusMode ? (
        <CompanySidebar
          collapsed={collapsed}
          mobileOpen={mobileOpen}
          onCollapse={() => setCollapsed((value) => !value)}
          onMobileClose={() => setMobileOpen(false)}
        />
      ) : null}
      <div className="min-w-0 flex-1">
        {!focusMode ? <TopCommandBar onOpenNavigation={() => setMobileOpen(true)} /> : null}
        <main
          className={cn(
            "min-w-0 p-4 md:p-6 xl:p-8",
            focusMode ? "min-h-screen" : "game-stage min-h-[calc(100vh-56px)] pb-28 md:pb-8",
          )}
        >
          <div className={cn("mx-auto w-full", focusMode ? "max-w-[1280px]" : "max-w-[1480px]")}>
            <Outlet />
          </div>
        </main>
        <TutorialOverlay />
        <TutorialHandoff />
        <div id="global-overlay-layer" />
      </div>
    </div>
  );
}
