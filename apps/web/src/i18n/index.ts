import i18next, { type InitOptions } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import commonZh from "./locales/zh-CN/common.json";
import navZh from "./locales/zh-CN/nav.json";
import authZh from "./locales/zh-CN/auth.json";
import appZh from "./locales/zh-CN/app.json";
import commonEn from "./locales/en-US/common.json";
import navEn from "./locales/en-US/nav.json";
import authEn from "./locales/en-US/auth.json";
import appEn from "./locales/en-US/app.json";

export const SUPPORTED_LANGUAGES = [
  { code: "zh-CN", label: "简体中文" },
  { code: "en-US", label: "English" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

export const LANGUAGE_STORAGE_KEY = "eidolon-language";

export const NAMESPACES = ["common", "nav", "auth", "app"] as const;

const resources = {
  "zh-CN": { common: commonZh, nav: navZh, auth: authZh, app: appZh },
  "en-US": { common: commonEn, nav: navEn, auth: authEn, app: appEn },
};

/** zh* → zh-CN (the product default), everything else → en-US. */
export function normalizeDetectedLanguage(lng: string): LanguageCode {
  return lng.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

export function i18nConfig(overrides: InitOptions = {}): InitOptions {
  return {
    resources,
    fallbackLng: "zh-CN",
    defaultNS: "common",
    ns: [...NAMESPACES],
    supportedLngs: ["zh-CN", "en-US"],
    returnNull: false,
    interpolation: { escapeValue: false },
    initAsync: false,
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
      convertDetectedLanguage: normalizeDetectedLanguage,
    },
    ...overrides,
  };
}

void i18next.use(LanguageDetector).use(initReactI18next).init(i18nConfig());

// Keep <html lang> in step so screen readers and CSS `:lang()` follow the UI language.
i18next.on("languageChanged", (lng) => {
  if (typeof document !== "undefined") document.documentElement.lang = lng;
});

export default i18next;
