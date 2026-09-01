import "@testing-library/jest-dom/vitest";
import { beforeAll } from "vitest";

// This vitest/jsdom combo provides NO window.localStorage (opaque origin),
// and Node 22+ ships an experimental global `localStorage` that emits an
// ExperimentalWarning when touched without `--localstorage-file`. Install a
// plain in-memory implementation on the window/global FIRST — before the
// i18n module loads — because the i18next LanguageDetector caches its
// storage-availability result on first use (a failed early probe disables
// the localStorage detector forever). The polyfill doubles as the storage
// the detector reads/writes, since it resolves to the same window global.
const memoryStorage = () => {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => void store.delete(key),
    setItem: (key: string, value: string) => void store.set(key, String(value)),
  } as Storage;
};

try {
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage(),
    configurable: true,
    writable: true,
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    value: memoryStorage(),
    configurable: true,
    writable: true,
  });
} catch {
  // Environment already provides storage; nothing to do.
}

// Dynamic (not hoisted) import so the polyfill above is installed before the
// default i18n instance initializes and probes storage.
const i18n = (await import("./src/i18n")).default;

// Deterministic baseline for all component tests: UI text asserts English
// strings (the jsdom navigator defaults to en-US anyway).
beforeAll(async () => {
  await i18n.changeLanguage("en-US");
});
