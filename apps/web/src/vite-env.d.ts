/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Optional backend base URL override (e.g. http://192.168.1.10:26881).
   * Empty/unset = same-origin relative paths (default; /api and /ws are proxied).
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
