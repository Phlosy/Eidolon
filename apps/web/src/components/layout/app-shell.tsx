import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CompanySidebar } from "./company-sidebar";
import { TopCommandBar } from "./top-command-bar";
import { useEventStream } from "../../hooks/useEventStream";
import { TutorialOverlay } from "../tutorial/tutorial-overlay";
import { TutorialHandoff } from "../tutorial/tutorial-handoff";

export function AppShell() {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
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
      <CompanySidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onCollapse={() => setCollapsed((value) => !value)}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="min-w-0 flex-1">
        <TopCommandBar onOpenNavigation={() => setMobileOpen(true)} />
        <main className="app-grid min-h-[calc(100vh-72px)] min-w-0 p-4 pb-28 md:p-6 md:pb-8 xl:p-8">
          <div className="mx-auto max-w-[1760px]">
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
