import { Pause, Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { OfficeEventBridge } from "../bridge/office-event-bridge";
import { authOfficeDemoSnapshot } from "../state/office-state-adapter";
import type { OfficeEmployeeState } from "../types/office-state";
import { AuthOfficeGameCanvas } from "./auth-office-game-canvas";

const translationKeyById = {
  "demo-engineer": "engineer",
  "demo-operations": "operations",
  "demo-project": "project",
  "demo-research": "research",
} as const;

export function AuthOfficeCover() {
  const { t } = useTranslation("auth");
  const bridge = useMemo(() => new OfficeEventBridge(), []);
  const [isDesktop, setIsDesktop] = useState(
    () =>
      typeof window === "undefined" ||
      !window.matchMedia ||
      window.matchMedia("(min-width: 1024px)").matches,
  );
  const [activeEmployeeId, setActiveEmployeeId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [paused, setPaused] = useState(
    () =>
      typeof window !== "undefined" &&
      Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
  );
  const snapshot = useMemo(
    () => ({
      ...authOfficeDemoSnapshot,
      employees: authOfficeDemoSnapshot.employees.map((employee) => {
        const key = translationKeyById[employee.id as keyof typeof translationKeyById];
        if (!key) return employee;
        return {
          ...employee,
          name: t(`shell.office.characters.${key}.name`),
          role: t(`shell.office.characters.${key}.role`),
          quote: t(`shell.office.characters.${key}.quote`),
        };
      }),
    }),
    [t],
  );
  const activeEmployee = snapshot.employees.find((employee) => employee.id === activeEmployeeId);
  const working = snapshot.employees.filter((employee) => employee.status === "WORKING").length;
  const meeting = snapshot.employees.filter((employee) => employee.status === "MEETING").length;

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia("(min-width: 1024px)");
    const sync = () => setIsDesktop(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(
    () =>
      bridge.on("office.employee.focus", ({ employeeId }) => {
        setActiveEmployeeId(employeeId);
      }),
    [bridge],
  );

  useEffect(
    () =>
      bridge.on("office.ready", () => {
        setReady(true);
        bridge.emit("office.motion.set", { paused });
      }),
    [bridge, paused],
  );

  useEffect(() => () => bridge.clear(), [bridge]);

  useEffect(() => {
    bridge.emit("office.motion.set", { paused });
  }, [bridge, paused]);

  useEffect(() => {
    const syncVisibility = () =>
      bridge.emit("office.motion.set", { paused: paused || document.hidden });
    document.addEventListener("visibilitychange", syncVisibility);
    return () => document.removeEventListener("visibilitychange", syncVisibility);
  }, [bridge, paused]);

  const focusEmployee = (employee: OfficeEmployeeState | null) => {
    const employeeId = employee?.id ?? null;
    setActiveEmployeeId(employeeId);
    bridge.emit("office.employee.focus", { employeeId });
  };

  if (!isDesktop) return null;

  return (
    <figure
      className={`auth-office-frame${paused ? " is-paused" : ""}`}
      data-testid="auth-office-game"
      aria-label={t("shell.office.alt")}
    >
      <figcaption className="auth-office-hud">
        <span className="auth-office-live-dot" aria-hidden="true" />
        <span>{t("shell.office.status")}</span>
        <span
          className="auth-office-stats"
          aria-label={t("shell.office.summary", { working, meeting })}
        >
          {t("shell.office.people", { count: snapshot.employees.length })}
          <span aria-hidden="true"> · </span>
          {t("shell.office.working", { count: working })}
          <span aria-hidden="true"> · </span>
          {t("shell.office.meeting", { count: meeting })}
        </span>
        <button
          type="button"
          className="auth-office-motion-toggle"
          aria-label={t(paused ? "shell.office.play" : "shell.office.pause")}
          aria-pressed={paused}
          onClick={() => setPaused((value) => !value)}
        >
          {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
        </button>
      </figcaption>

      <div className="auth-office-stage">
        <AuthOfficeGameCanvas
          bridge={bridge}
          initialState={snapshot}
          label={t("shell.office.canvasAlt")}
        />
        <div className={`auth-office-loading${ready ? " is-ready" : ""}`} aria-hidden={ready}>
          {t("shell.office.loading")}
        </div>
        <div
          className={`auth-office-dialogue${activeEmployee ? " is-visible" : ""}`}
          aria-live="polite"
          aria-hidden={!activeEmployee}
        >
          {activeEmployee ? (
            <>
              <p className="auth-office-dialogue-name">
                {activeEmployee.name} <span>{activeEmployee.role}</span>
              </p>
              <p>{activeEmployee.quote}</p>
            </>
          ) : null}
        </div>
        <div className="auth-office-roster" aria-label={t("shell.office.rosterLabel")}>
          {snapshot.employees.map((employee) => (
            <button
              key={employee.id}
              type="button"
              className={`auth-office-person-button${employee.id === activeEmployeeId ? " is-speaking" : ""}`}
              aria-label={t("shell.office.personLabel", {
                name: employee.name,
                role: employee.role,
              })}
              aria-expanded={employee.id === activeEmployeeId}
              onBlur={() => focusEmployee(null)}
              onClick={() => focusEmployee(employee)}
              onFocus={() => focusEmployee(employee)}
              onPointerEnter={() => focusEmployee(employee)}
              onPointerLeave={() => focusEmployee(null)}
            >
              {employee.name.slice(0, 1)}
            </button>
          ))}
        </div>
        <p className="auth-office-hint">{t("shell.office.hint")}</p>
      </div>
    </figure>
  );
}
