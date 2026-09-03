import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-sc";
import "@fontsource-variable/jetbrains-mono";
import App from "./App";
import { applyTheme, useThemeStore } from "./stores/theme";
import "./i18n";
import "./index.css";

// Apply the persisted theme before first paint (default dark).
applyTheme(useThemeStore.getState().theme);

const queryClient = new QueryClient({
  // 教程完成只由真实业务状态决定，所以"什么时候该重看进度"也必须是事件驱动：
  // 任何一个业务 mutation 成功，都顺手重取一次教程进度。
  // （轮询仍在，但降级为 45s 的丢事件兜底，不再是推进的主路径。）
  mutationCache: new MutationCache({
    onSuccess: (_data, _variables, _context, mutation) => {
      if (mutation.meta?.tutorialOwned) return;
      void queryClient.invalidateQueries({ queryKey: ["tutorial"] });
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
