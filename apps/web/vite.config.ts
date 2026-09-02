/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// No @types/node in this project — minimal declaration for env access.
declare const process: { env: Record<string, string | undefined> };

const webPort = Number(process.env.EIDOLON_WEB_PORT ?? 26880);
const apiPort = process.env.EIDOLON_API_PORT ?? "26881";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: webPort,
    // Hard requirement: never silently drift to another port — fail loudly instead,
    // so the API port (26881) can never be shadowed by a second dev server.
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
      },
      "/ws": {
        target: `ws://127.0.0.1:${apiPort}`,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});
