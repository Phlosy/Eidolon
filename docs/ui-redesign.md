# Eidolon Web UI Redesign

## 0. Scope and baseline

This redesign treats the existing application as a functional reference, not a visual reference. The API client, TanStack Query hooks, Zustand stores, TypeScript contracts, lifecycle flows, i18n, WebSocket invalidation, React Flow workflow, and business mutations remain authoritative. Layout, navigation, page composition, visual components, and presentation models may be replaced.

Baseline recorded on 2026-09-02 from the existing `dev` worktree:

- `make lint`: passed.
- `make test`: passed — 93 server tests and 88 web tests.
- `make build`: passed.
- Baseline web bundle: one 828.95 kB application JS chunk (252.35 kB gzip) and 179.59 kB CSS (58.36 kB gzip). Vite reports a chunk-size warning.

The worktree already contains uncommitted product work. The redesign must preserve it and must not reset, overwrite, or silently remove those business capabilities.

## 1. Product and visual position

Eidolon becomes an **AI Company Operating System**: a company-management simulation, collaborative workspace, and live AI-organization command center.

The visual ratio is:

- 70% durable enterprise software: legible, predictable, information-dense, keyboard accessible.
- 20% management game: levels, progress tracks, missions, roster language, company pulse.
- 10% light science fiction: luminous status edges, subtle grid/radar motifs, system telemetry.

The interface must communicate that the company is alive. State changes, employee work, project motion, runtime health, and asset production are first-class visual events. It must avoid cyberpunk excess, neon pollution, decorative particles, or a game HUD that weakens everyday usability.

## 2. Information architecture

The product is organized around six operational domains:

1. **Command** — company-wide status and the spatial office view.
2. **Workforce** — employee roster, employee workbenches, departments, lifecycle.
3. **Work** — project missions, tasks, milestones, and workflow graphs.
4. **Assets** — company drive, artifacts, knowledge, and Git production.
5. **Infrastructure** — runtimes, models, providers, sessions, and updates.
6. **System** — organization preferences, appearance, language, access packages, API details.

Existing routes remain stable. New visual destinations may initially resolve to an existing capability surface when the backend does not yet expose a dedicated page.

## 3. Navigation structure

`AppShell` owns the persistent application frame:

```text
AppShell
├── CompanySidebar
│   ├── CompanyIdentity
│   ├── CommandNavigation
│   ├── WorkforceNavigation
│   ├── WorkNavigation
│   ├── AssetsNavigation
│   ├── InfrastructureNavigation
│   └── CompanyPresence
├── TopCommandBar
│   ├── Breadcrumb / route context
│   ├── Global search affordance
│   ├── WebSocket live state
│   ├── Active employee count
│   ├── Create action
│   ├── Language
│   └── Theme
├── MainWorkspace
└── GlobalOverlayLayer
```

Desktop sidebar width is 272 px expanded and 80 px collapsed. At smaller desktop widths it collapses without reducing the workspace below a usable command-center canvas. Navigation uses Lucide SVG icons, section labels, active rails, contextual badges, and minimum 44 px interaction targets.

## 4. Dashboard information architecture

The root route becomes `CompanyOverviewPage`, not a row of statistics followed by a log.

```text
CompanyOverviewPage
├── CompanyHero
│   ├── operational state
│   ├── derived company level and XP
│   ├── live workforce / project / task meters
│   └── infrastructure and asset snapshot
├── CompanyPulse
├── WorkforceOverview
├── ActiveProjectBoard
├── InfrastructureStatus
├── AssetProduction
└── AlertCenter
```

The hero is an asymmetric operational surface with a company identity block, a large status readout, and resource rails. Panels below use varied structures: roster, mission board, timeline, meters, and alert stack.

Data provenance rules:

- Online employees, project counts, tasks, documents, runtime instances, and events come from existing APIs.
- Company level and XP are deterministic derived UI values computed from real counts; they are labeled as derived.
- Runtime cost remains the existing mock-mode estimate and is labeled estimated.
- No production view uses `Math.random()`.

## 5. Office page

`CompanyOfficeScene` is a responsive 2D floor plan, not an employee-card grid. Department zones form spatial rooms connected by a shared operations corridor. Each zone shows capacity, live count, and a compact employee presence node.

Employees are positioned inside their department zone with avatar, role, status, and current-task signal. Working, learning, researching, meeting, idle, offline, and error states have distinct semantic colors and restrained motion. Selecting an employee opens an in-page quick panel with links to the full workbench; it does not change lifecycle behavior.

## 6. Employee list

The roster combines HR operations with role-management language:

- Grid and list views share the same data model.
- Filters cover status, department, and text search.
- Each entry shows identity, title, department, lifecycle, runtime, current state/task, workload, derived level, and skill summary when available.
- Hire, transfer, suspend, resume, and offboard continue to use existing lifecycle hooks and dialogs.

The selected view is presentation state only and can be persisted locally later without affecting APIs.

## 7. Employee detail

Employee detail becomes a workbench with a persistent `EmployeeHero` and task-oriented tab rail.

The hero includes avatar, name, title, department, derived level/XP, lifecycle, runtime/provider, live status, and current task. Existing capabilities map to:

- Overview — profile, operational snapshot, lifecycle timeline.
- Work — current work and activity.
- Skills — skill bars, validation, success rate, growth signals.
- Learning — priorities, learning records, and knowledge proposals.
- Memory / Knowledge — existing records.
- Workspace / Git / Documents — current workspace and asset ownership.
- Runtime — runtime configuration and controls.
- Access — accounts, entitlements, assets, employment.
- Career / Activity — lifecycle and performance history.

Tabs unavailable from current data are not populated with invented records; existing tabs remain reachable while presentation is reorganized.

## 8. Projects

The project index becomes a `ProjectCommandBoard`. Each project is a mission surface with status, owner, progress, health, milestone/task counts, artifacts, blockers, and last activity. Health and progress are deterministic derived values from existing project/task status.

Project detail keeps React Flow but frames it as a mission workflow: requirement, research, design, development, QA, and release nodes. Node state, employee ownership, duration, and artifacts appear when provided by the API; missing fields remain absent rather than simulated.

## 9. Runtime and providers

Infrastructure is presented as an `InfrastructureControlCenter`:

- Runtime nodes show type, employee binding, container state, model/provider, image version, update state, sessions, and available health fields.
- Provider inventory remains backed by the current provider APIs.
- Start, stop, restart, create, update, and test actions retain their existing mutations and confirmation semantics.
- Runtime and provider sections gain a dedicated route-level surface while existing employee/runtime and settings workflows remain functional.

## 10. Cloud documents, Git, artifacts, and knowledge

The existing Drive remains the canonical document source. Its new shell resembles a company document center with spaces, recent documents, employee contributions, and project context while preserving tree browsing, filtering, folder creation, viewing, editing, and revisions.

Git adopts developer-workspace language: repositories/connections, built-in Gitea state, contributors, branches, and recent activity where available. Artifact categories map to Drive document types and are labeled as production assets, not recreated as a parallel legacy module.

## 11. Settings

Settings becomes a grouped system console with a local section navigator:

- Organization and environment.
- Providers.
- Access packages.
- Git services.
- Runtime images and updates.
- Appearance and language.
- API and diagnostics.

Complex operational inventories use control-center panels; simple preferences use compact form sections. Existing components and mutations are retained behind the new shell.

## 12. Design system

The design source of truth lives in `src/design/`:

```text
src/design/
├── tokens.css
├── typography.css
├── motion.ts
├── status.ts
└── theme.ts
```

Token groups:

- Foundations: `--background`, `--surface`, `--surface-elevated`, `--surface-interactive`.
- Boundaries: `--border`, `--border-active`.
- Brand: `--primary`, `--secondary`, `--accent`.
- Feedback: `--success`, `--warning`, `--danger`, `--info`.
- Employee states: `--employee-working`, `--employee-learning`, `--employee-idle`, `--employee-error`, plus the existing extended states.
- Effects: `--glow-primary`, `--glow-success`, `--shadow-panel`, `--shadow-floating`.
- Geometry: `--radius-panel`, `--radius-card`, shared spacing and control heights.

Typography remains Inter for Latin, Noto Sans SC for Chinese, and JetBrains Mono for telemetry/code. Body type never drops below 12 px; normal reading content targets 14–16 px with 1.5 line height. All colors and visual parameters are semantic tokens, not raw values scattered through business components.

## 13. Component architecture

```text
components/
├── layout/       AppShell, CompanySidebar, TopCommandBar, WorkspaceHeader
├── company/      CompanyHero, CompanyPulse, WorkforceOverview, AlertCenter
├── employee/     EmployeeAvatar, Status, Card, Hero, SkillPanel, QuickPanel
├── office/       OfficeScene, DepartmentZone, OfficeEmployee
├── project/      ProjectCard, CommandBoard, Health, Workflow
├── runtime/      RuntimeNode, RuntimeStatus, InfrastructureOverview
├── activity/     ActivityTimeline, ActivityEvent
├── game/         LevelBadge, XPBar, StatusMeter, ResourceMeter, MissionProgress
└── shared/       Panel, SectionHeader, Empty/Loading/Error/OfflineState, LiveBadge
```

Business hooks do not import presentation components. Derived UI selectors/models live close to their domain hooks and are pure, deterministic, and unit-testable. Page modules compose domain components; they do not become multi-thousand-line monoliths.

## 14. Migration plan

1. Establish tokens, typography, motion, status metadata, and shared primitives.
2. Replace `AppLayout` with `AppShell`, grouped navigation, command bar, and lazy routes.
3. Replace the dashboard with the company command center.
4. Replace Office with the interactive 2D scene and employee quick panel.
5. Rebuild employee roster with grid/list modes and lifecycle-preserving actions.
6. Rebuild employee detail hero, navigation, skill, learning, and workbench surfaces.
7. Rebuild project index and reframe the existing React Flow graph.
8. Reframe Drive, Git, and production assets.
9. Add infrastructure control-center routing and reframe providers/runtimes.
10. Reframe settings, remove dead presentation code, and complete final QA.

Each phase must remain buildable. Old presentation components are deleted only after their consumers migrate. API/hooks/types/stores are changed only when a deterministic derived model or missing presentation query requires it.

## 15. Animation strategy

Motion communicates state change:

- Fast (120–160 ms): button, icon, and focus feedback.
- Normal (220–320 ms): panel hover, overlay, navigation, and selection.
- Slow (420–600 ms): page/panel entrance and progress interpolation.
- Ambient: only live status pulse, at low amplitude and limited to active states.

No large floating loops, parallax, particle fields, or width/height layout animation. Transforms and opacity are preferred. New activity can enter once; progress can interpolate; live dots can breathe. `prefers-reduced-motion` renders the final state with effectively no animation.

## 16. Responsive and accessibility strategy

The primary canvases are 1440p and 1080p, with 1280 px as the minimum fully expanded command-center layout.

- At 1280 px, grids reduce columns and the sidebar may collapse.
- Below 1024 px, the sidebar becomes an overlay/rail and multi-column command panels stack.
- Long English labels wrap or truncate with accessible titles; controls never depend on Chinese string length.
- No horizontal page scrolling; intentionally scrollable tables/rails disclose that behavior.
- Controls target at least 44×44 px where practical, preserve visible focus, and include accessible labels for icon-only actions.
- Text/background contrast targets WCAG AA (4.5:1 for normal text).
- Loading, empty, error, offline, and disconnected states use a unified status-panel vocabulary.

## Definition of done

- The first view is unmistakably a live AI company command center, not a statistic-card dashboard.
- Overview contains company hero, workforce, projects, pulse, infrastructure, assets, and alerts.
- Office is spatial and interactive.
- Employee and project experiences communicate roles, growth, missions, and operational state without inventing backend facts.
- Drive, Git, runtime/provider, settings, lifecycle, WebSocket, theme, i18n, and all current APIs remain functional.
- Major routes are lazy loaded and the initial JS chunk is materially smaller than baseline.
- `make lint`, `make test`, and `make build` pass.
- Desktop and compact-window browser QA is captured with screenshots.
