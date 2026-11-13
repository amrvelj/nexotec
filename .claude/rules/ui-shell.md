---
paths:
  - "frontend/packages/ui-kit/src/shell/**"
  - "frontend/apps/dms/src/App.tsx"
  - "frontend/apps/dms/src/main.tsx"
  - "frontend/apps/dms/src/layout/**"
  - "frontend/apps/dms/src/config/**"
  - "frontend/apps/dms/src/pages/Dashboard*"
  - "frontend/apps/dms/src/pages/Legal*"
  - "frontend/apps/dms/src/pages/legal/**"
  - "frontend/apps/dms/src/pages/DetailBreadcrumb*"
  - "frontend/apps/dms/src/architecture/lazy-routes*"
---
<!-- Maintainer note. UI/UX Specification (Application Shell), FR-UI-07, ADR-084, ADR-108. Split out
of ui.md by KAN-615 (2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# The application shell

- **The dashboard is the landing page and the breadcrumb's root** (FR-UI-07): the shell passes
  `topbar.breadcrumbRoot` (`shell.nav.dashboard`, `/`) and its router `Link`; the kit's `Topbar`
  puts it first on every screen as a link — drawn as the dashboard icon alone, the label its
  accessible name and tooltip — and names the dashboard once on the dashboard itself. A page's own
  segments: `ui-detail.md`. Account chrome lives only in the sidebar footer; the top bar carries
  the breadcrumb and search only.
- **ADR-084 — one environment-notice slot.** `AppShell`'s optional `notice` strip (ui-kit
  `shell/EnvironmentNotice.tsx`, between the top bar and the content, `role="status"`, no dismiss)
  is the only place for a persistent deployment-level notice — D-A-05's support-access banner uses
  it when built, never a second banner. A build-time setting switches it on
  (`VITE_OUTBOX_WORKER_ABSENT=true` in `render.yaml`, `src/config/deployment.ts`), never the
  environment's name; its text is an i18n key in all four bundles.
- **Every screen but the sign-in loads on demand:** a new route's page goes into `App.tsx` through
  `lazyPage(() => import('./pages/X'), 'X')`, never a static import. `LoginPage`, `ProtectedRoute`,
  `RequireEntitlement`, `AuthProvider`, `DmsShell` and `RouteSuspense` stay eager, and
  `src/architecture/lazy-routes.test.ts` checks that none of them, nor `App.tsx` beyond
  `LoginPage`, imports a page statically. `RouteSuspense` shows the loading state on a hard load
  and a translated reload screen when a chunk cannot be fetched (a tab older than the deploy). On
  in-app navigation the previous screen stays up until the next chunk arrives — never blank, no
  loader (React Router 7's `startTransition`; no render test pins it).
- **Public legal pages** (ADR-108): `/legal/terms` and `/legal/privacy` are the only routes besides
  the sign-in screens reachable without a session — lazy routes outside the signed-in layout, text
  from `legal.*` in all four bundles, `?lang=` picks the language. The sign-in screens link them
  below the card; signed in, they sit at the foot of the sidebar's account menu
  (`Sidebar.footerLinks`, labels from the app, new tab). Every section is a marked draft until
  counsel reviews it (A-5) — **never remove the draft banner by hand**.
