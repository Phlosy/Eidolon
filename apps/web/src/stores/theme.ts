import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "dark" | "light";

interface ThemeState {
  theme: Theme;
  toggleTheme: () => void;
}

/** Class strategy on <html> (default dark), persisted via localStorage. */
export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: "dark",
      toggleTheme: () => set((state) => ({ theme: state.theme === "dark" ? "light" : "dark" })),
    }),
    { name: "eidolon-theme" },
  ),
);

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}
