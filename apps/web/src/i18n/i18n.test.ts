import { describe, expect, it, beforeEach } from "vitest";
import i18next, { type InitOptions } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
import {
  i18nConfig,
  LANGUAGE_STORAGE_KEY,
  normalizeDetectedLanguage,
  SUPPORTED_LANGUAGES,
} from "./index";
import { enumLabel } from "../utils/labels";

/**
 * Isolated i18n instance (no shared state with the app's default instance).
 * `detection: undefined` disables the browser detector so `lng` is the
 * single source of truth for these tests.
 */
async function createInstance(options: InitOptions = {}) {
  const instance = i18next.createInstance().use(initReactI18next);
  await instance.init(i18nConfig({ detection: undefined, ...options }));
  return instance;
}

/** Instance WITH the browser language detector (for persistence/detection tests). */
async function createDetectingInstance(options: InitOptions = {}) {
  const instance = i18next.createInstance().use(LanguageDetector).use(initReactI18next);
  await instance.init(i18nConfig(options));
  return instance;
}

/** i18nConfig resources with one en-US key removed (to exercise fallbackLng). */
function resourcesWithoutEnKey(namespace: string, key: string): InitOptions["resources"] {
  const base = i18nConfig();
  const resources = structuredClone(base.resources) as Record<
    string,
    Record<string, Record<string, unknown>>
  >;
  delete resources["en-US"][namespace][key];
  return resources as InitOptions["resources"];
}

// The detector reads/writes `window.localStorage` (jsdom's) — clear the
// same store it uses so no test leaks a persisted language into the next.
beforeEach(() => {
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
});

describe("i18n infrastructure", () => {

  it("registers the two officially supported languages", () => {
    const codes = SUPPORTED_LANGUAGES.map((l) => l.code);
    expect(codes).toContain("zh-CN");
    expect(codes).toContain("en-US");
    expect(new Set(codes).size).toBe(codes.length);
  });

  it("normalizes browser languages: zh* → zh-CN, everything else → en-US", () => {
    expect(normalizeDetectedLanguage("zh-CN")).toBe("zh-CN");
    expect(normalizeDetectedLanguage("zh-TW")).toBe("zh-CN");
    expect(normalizeDetectedLanguage("zh")).toBe("zh-CN");
    expect(normalizeDetectedLanguage("en-US")).toBe("en-US");
    expect(normalizeDetectedLanguage("en")).toBe("en-US");
    expect(normalizeDetectedLanguage("fr-FR")).toBe("en-US");
    expect(normalizeDetectedLanguage("ja-JP")).toBe("en-US");
    expect(normalizeDetectedLanguage("ko-KR")).toBe("en-US");
  });
});

describe("language switching", () => {
  it("translates the same key in zh-CN and en-US", async () => {
    const instance = await createInstance({ lng: "zh-CN" });
    expect(instance.t("nav:dashboard")).toBe("仪表盘");
    expect(instance.t("project:newProject")).toBe("新建项目");

    await instance.changeLanguage("en-US");
    expect(instance.t("nav:dashboard")).toBe("Dashboard");
    expect(instance.t("project:newProject")).toBe("New Project");
  });

  it("re-resolves enum labels after a switch (status labels)", async () => {
    const instance = await createInstance({ lng: "zh-CN" });
    expect(instance.t("employee:status.working")).toBe("工作中");
    expect(instance.t("project:status.in_progress")).toBe("进行中");
    await instance.changeLanguage("en-US");
    expect(instance.t("employee:status.working")).toBe("Working");
    expect(instance.t("project:status.in_progress")).toBe("In Progress");
  });

  it("supports plural forms (office member count)", async () => {
    const instance = await createInstance({ lng: "en-US" });
    expect(instance.t("office:memberCount", { count: 1 })).toBe("1 employee");
    expect(instance.t("office:memberCount", { count: 5 })).toBe("5 employees");
  });
});

describe("LocalStorage persistence", () => {
  it("persists the chosen language and restores it on the next load", async () => {
    // First visit: no persisted choice → browser language (en-US in jsdom).
    const first = await createDetectingInstance();
    expect(first.resolvedLanguage).toBe("en-US");

    // User picks zh-CN → detector caches it under the storage key.
    await first.changeLanguage("zh-CN");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh-CN");

    // "Reload": a fresh instance must restore zh-CN from storage.
    const second = await createDetectingInstance();
    expect(second.resolvedLanguage).toBe("zh-CN");
    expect(second.t("nav:dashboard")).toBe("仪表盘");
  });

  it("prefers the persisted choice over the browser language", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
    const instance = await createDetectingInstance();
    expect(instance.resolvedLanguage).toBe("zh-CN");
  });
});

describe("language detection & fallback", () => {
  it("maps a Chinese browser language (zh-TW) to the zh-CN product default", async () => {
    // The detector prefers navigator.languages[0] over navigator.language,
    // so stub both.
    const langs = Object.getOwnPropertyDescriptor(navigator, "languages");
    const lang = Object.getOwnPropertyDescriptor(navigator, "language");
    Object.defineProperty(navigator, "languages", { value: ["zh-TW"], configurable: true });
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
    try {
      const instance = await createDetectingInstance();
      expect(instance.resolvedLanguage).toBe("zh-CN");
    } finally {
      if (langs) Object.defineProperty(navigator, "languages", langs);
      if (lang) Object.defineProperty(navigator, "language", lang);
    }
  });

  it("maps any non-Chinese browser language to en-US", async () => {
    const langs = Object.getOwnPropertyDescriptor(navigator, "languages");
    const lang = Object.getOwnPropertyDescriptor(navigator, "language");
    Object.defineProperty(navigator, "languages", { value: ["fr-FR"], configurable: true });
    Object.defineProperty(navigator, "language", { value: "fr-FR", configurable: true });
    try {
      const instance = await createDetectingInstance();
      expect(instance.resolvedLanguage).toBe("en-US");
    } finally {
      if (langs) Object.defineProperty(navigator, "languages", langs);
      if (lang) Object.defineProperty(navigator, "language", lang);
    }
  });

  it("falls back to the zh-CN value when a key is missing in en-US", async () => {
    const instance = await createInstance({
      lng: "en-US",
      resources: resourcesWithoutEnKey("dashboard", "title"),
    });
    expect(instance.t("dashboard:title")).toBe("仪表盘");
  });
});

describe("missing key behavior", () => {
  it("renders the key itself when the key is missing everywhere (never crashes)", async () => {
    const instance = await createInstance({ lng: "en-US" });
    // Default-namespace lookup returns the full key path…
    expect(instance.t("does.not.exist")).toBe("does.not.exist");
    // …explicit-namespace lookups return the key with the `ns:` prefix
    // stripped. enumLabel() detects both shapes and humanizes the fallback.
    expect(instance.t("project:noSuchKey")).toBe("noSuchKey");
    expect(enumLabel(instance.t.bind(instance), "project:status", "mystery_status")).toBe(
      "Mystery Status",
    );
  });
});
