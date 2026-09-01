# @eidolon/web

React SPA for Eidolon — the AI Company Command Center.

## Development

```bash
pnpm install
pnpm dev        # vite --host 0.0.0.0 (port 26880, from vite.config.ts / EIDOLON_WEB_PORT)
```

By default the SPA calls the API via same-origin relative paths (`/api/v1/...`);
the Vite dev server proxies `/api` and `/ws` to `127.0.0.1:26881`
(`EIDOLON_API_PORT`). The WebSocket URL (`/ws/events`) derives ws/wss from
`window.location`. `VITE_API_BASE_URL` is an optional escape hatch for a
non-same-origin backend. See `../../docs/architecture.md` §8 for the API
contract and §3.3 for the shared enum string contracts mirrored in
`src/types/`.

## Scripts

- `pnpm dev` — dev server (bound to 0.0.0.0:26880)
- `pnpm build` — `tsc --noEmit && vite build`
- `pnpm test` — vitest run
- `pnpm lint` — eslint
- `pnpm preview` — preview the production build
