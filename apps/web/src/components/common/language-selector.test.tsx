import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import i18n, { LANGUAGE_STORAGE_KEY } from "../../i18n";
import { LanguageSelector } from "./language-selector";

describe("LanguageSelector", () => {
  beforeEach(async () => {
    // Detector cache lives on window.localStorage (jsdom's store).
    window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    await i18n.changeLanguage("en-US");
  });

  it("shows the current language and switches it immediately", async () => {
    render(<LanguageSelector />);
    const select = screen.getByRole("combobox", { name: "Language" });
    expect(select).toHaveValue("en-US");

    fireEvent.change(select, { target: { value: "zh-CN" } });

    await waitFor(() => expect(select).toHaveValue("zh-CN"));
    // Translation layer switched with it.
    expect(i18n.t("nav:dashboard")).toBe("仪表盘");
    // <html lang> stays in sync (a11y).
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("persists the choice to LocalStorage for the next visit", async () => {
    render(<LanguageSelector />);
    fireEvent.change(screen.getByRole("combobox", { name: "Language" }), {
      target: { value: "zh-CN" },
    });
    await waitFor(() => expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh-CN"));
  });

  it("switches back to English and updates the persisted value", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
    await i18n.changeLanguage("zh-CN");

    render(<LanguageSelector />);
    const select = screen.getByRole("combobox", { name: "语言" });
    expect(select).toHaveValue("zh-CN");

    fireEvent.change(select, { target: { value: "en-US" } });
    await waitFor(() =>
      expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en-US"),
    );
    expect(i18n.t("nav:dashboard")).toBe("Dashboard");
  });
});
