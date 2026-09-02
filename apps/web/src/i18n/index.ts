import i18next, { type InitOptions } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import commonZh from "./locales/zh-CN/common.json";
import navZh from "./locales/zh-CN/nav.json";
import dashboardZh from "./locales/zh-CN/dashboard.json";
import officeZh from "./locales/zh-CN/office.json";
import employeeZh from "./locales/zh-CN/employee.json";
import projectZh from "./locales/zh-CN/project.json";
import driveZh from "./locales/zh-CN/drive.json";
import runtimeZh from "./locales/zh-CN/runtime.json";
import providerZh from "./locales/zh-CN/provider.json";
import gitZh from "./locales/zh-CN/git.json";
import settingsZh from "./locales/zh-CN/settings.json";
import eventZh from "./locales/zh-CN/event.json";
import lifecycleZh from "./locales/zh-CN/lifecycle.json";
import authZh from "./locales/zh-CN/auth.json";

import commonEn from "./locales/en-US/common.json";
import navEn from "./locales/en-US/nav.json";
import dashboardEn from "./locales/en-US/dashboard.json";
import officeEn from "./locales/en-US/office.json";
import employeeEn from "./locales/en-US/employee.json";
import projectEn from "./locales/en-US/project.json";
import driveEn from "./locales/en-US/drive.json";
import runtimeEn from "./locales/en-US/runtime.json";
import providerEn from "./locales/en-US/provider.json";
import gitEn from "./locales/en-US/git.json";
import settingsEn from "./locales/en-US/settings.json";
import eventEn from "./locales/en-US/event.json";
import lifecycleEn from "./locales/en-US/lifecycle.json";
import authEn from "./locales/en-US/auth.json";

/**
 * Supported UI languages. Adding a language = create `locales/<code>/` with
 * the same per-module namespaces as below + one entry here.
 */
export const SUPPORTED_LANGUAGES = [
  { code: "zh-CN", label: "简体中文" },
  { code: "en-US", label: "English" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

/** localStorage key the detector reads/writes for the persisted language choice. */
export const LANGUAGE_STORAGE_KEY = "eidolon-language";

export const NAMESPACES = [
  "common",
  "nav",
  "dashboard",
  "office",
  "employee",
  "project",
  "drive",
  "runtime",
  "provider",
  "git",
  "settings",
  "event",
  "lifecycle",
  "auth",
] as const;

const resources = {
  "zh-CN": {
    common: commonZh,
    nav: navZh,
    dashboard: dashboardZh,
    office: officeZh,
    employee: employeeZh,
    project: projectZh,
    drive: driveZh,
    runtime: runtimeZh,
    provider: providerZh,
    git: gitZh,
    settings: settingsZh,
    event: eventZh,
    lifecycle: lifecycleZh,
    auth: authZh,
  },
  "en-US": {
    common: commonEn,
    nav: navEn,
    dashboard: dashboardEn,
    office: officeEn,
    employee: employeeEn,
    project: projectEn,
    drive: driveEn,
    runtime: runtimeEn,
    provider: providerEn,
    git: gitEn,
    settings: settingsEn,
    event: eventEn,
    lifecycle: lifecycleEn,
    auth: authEn,
  },
};

/**
 * Detection mapping: any navigator language starting with `zh` → zh-CN
 * (the product default), everything else → en-US. Applied to both the
 * persisted localStorage value and the navigator result, so a stored
 * "zh-CN"/"en-US" passes through unchanged.
 */
export function normalizeDetectedLanguage(lng: string): LanguageCode {
  return lng.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

/**
 * Missing-key convention: `returnNull: false` + `fallbackLng: "zh-CN"`.
 * A key missing in the active language falls back to the zh-CN value; a key
 * missing everywhere renders the key itself (visible in dev, never crashes).
 */
export function i18nConfig(overrides: InitOptions = {}): InitOptions {
  return {
    resources,
    fallbackLng: "zh-CN",
    defaultNS: "common",
    ns: [...NAMESPACES],
    // Only the codes we ship are selectable; anything else resolves to the
    // detection mapping (zh* → zh-CN, otherwise en-US).
    supportedLngs: ["zh-CN", "en-US"],
    returnNull: false,
    interpolation: { escapeValue: false },
    // Resources are bundled: resolve immediately so `t()` never returns raw
    // keys on the very first render (no key-flash before init settles).
    initAsync: false,
    detection: {
      // localStorage (explicit user choice) wins over the browser language.
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
      convertDetectedLanguage: normalizeDetectedLanguage,
    },
    ...overrides,
  };
}

/** Keep <html lang> in sync with the active UI language (a11y + font shaping). */
export function applyDocumentLang(lng: string): void {
  document.documentElement.lang = normalizeDetectedLanguage(lng);
}

/** The app-wide i18n instance. Tests may create isolated instances via i18nConfig. */
const i18n = i18next.createInstance().use(LanguageDetector).use(initReactI18next);
void i18n.init(i18nConfig());

i18n.on("languageChanged", (lng) => applyDocumentLang(lng));
applyDocumentLang(i18n.resolvedLanguage ?? i18n.language ?? "zh-CN");

export default i18n;
