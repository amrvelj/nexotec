import { useEffect, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle,
  Car,
  CarFront,
  ChartColumn,
  Cog,
  Handshake,
  LayoutDashboard,
  Receipt,
  ShieldCheck,
  Store,
  Users,
  Warehouse,
  Wrench,
} from 'lucide-react'
import { AppShell, OverlayProvider, purple, white, type GlobalSearchGroup, type GlobalSearchProps, type NavGroupConfig } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { UiPreferencesProvider, useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api } from '../api/client'
import { customerName } from '../utils/customer'
import type { CustomerPage, VehicleSearchResult } from '../api/types'

// Translated at render time (not a module-level constant) — labels must
// re-render when the user switches UI language via the top bar.
function buildNavGroups(t: (key: string) => string): NavGroupConfig[] {
  return [
    {
      // No `label` — a single top-level entry above Master Data, not its
      // own labelled group (§ Sidebar: "a sidebar entry above Master
      // Data", not a section of one).
      items: [{ label: t('shell.nav.dashboard'), href: '/', icon: LayoutDashboard, status: 'active' }],
    },
    {
      label: t('shell.nav.masterData'),
      items: [
        { label: t('shell.nav.customers'), href: '/customers', icon: Users, status: 'active' },
        { label: t('shell.nav.vehicles'), href: '/vehicles', icon: Car, status: 'active' },
        { label: t('shell.nav.partners'), href: '/partners', icon: Store, status: 'soon' },
        // WP-5 PR-8 (FR-V-11) — platform_admin-only in practice (the API
        // 403s for anyone else); not client-side role-gated in the nav
        // itself, matching this project's "server-side enforcement, UI
        // hiding is not a control" posture (Risk R-4).
        { label: t('shell.nav.mappingGaps'), href: '/vehicle-mdm/mapping-gaps', icon: AlertTriangle, status: 'active' },
      ],
    },
    {
      label: t('shell.nav.modules'),
      items: [
        // WP-8 PR-1: promotes the scaffolded "soon" slot — same shape as
        // WP-7 PR-1's own inventory promotion above.
        { label: t('shell.nav.sales'), href: '/sales', icon: Handshake, status: 'active' },
        // WP-8 PR-9 — "Bewertungen," beneath Verkauf: a judgment call (the
        // prototype confirms only the route #/valuations, not its nav
        // placement) since a valuation is created from within an offer's
        // trade-in container as often as it is from this standalone list.
        { label: t('shell.nav.valuations'), href: '/valuations', icon: CarFront, status: 'active' },
        { label: t('shell.nav.aftersales'), href: '/aftersales', icon: Wrench, status: 'soon' },
        // WP-7 PR-1: promotes the scaffolded "soon" slot — href moves from
        // /inventory to /stock, matching the reference prototype's own
        // route. Label stays "shell.nav.inventory" as-is (already "Lager"
        // in de.json, matching the prototype's own label exactly; EN/FR/IT
        // keep their existing strings).
        { label: t('shell.nav.inventory'), href: '/stock', icon: Warehouse, status: 'active' },
        { label: t('shell.nav.parts'), href: '/parts', icon: Cog, status: 'soon' },
        { label: t('shell.nav.finance'), href: '/finance', icon: Receipt, status: 'soon' },
        { label: t('shell.nav.reporting'), href: '/reporting', icon: ChartColumn, status: 'soon' },
        { label: t('shell.nav.compliance'), href: '/compliance', icon: ShieldCheck, status: 'soon' },
      ],
    },
  ]
}

const GLOBAL_SEARCH_LIMIT_PER_ENTITY = 5

/**
 * § FR-UI-08 — cross-entity search over the two entities that exist today
 * (customers, vehicles). Both `/customers?q=` and `/vehicle-mdm/search?q=`
 * already exist as ordinary read endpoints; this composes their existing
 * results for display and does not add any new backend behaviour — no
 * dedicated cross-entity search endpoint exists yet, and building one is
 * out of this package's scope (WP-6c owns presentation, not new APIs).
 * Each future module (sales, aftersales, ...) adds its own branch here
 * once it has something worth finding.
 */
function buildGlobalSearch(t: (key: string) => string, navigate: (path: string) => void): GlobalSearchProps {
  return {
    placeholder: t('shell.globalSearch.placeholder'),
    recentsLabel: t('shell.globalSearch.recents'),
    noResultsLabel: t('shell.globalSearch.noResults'),
    errorLabel: t('shell.globalSearch.error'),
    onSearch: async (query) => {
      const [customers, vehicles] = await Promise.all([
        api.get<CustomerPage>(`/customers?q=${encodeURIComponent(query)}&limit=${GLOBAL_SEARCH_LIMIT_PER_ENTITY}`),
        api.get<VehicleSearchResult>(`/vehicle-mdm/search?q=${encodeURIComponent(query)}`),
      ])
      const groups: GlobalSearchGroup[] = [
        {
          key: 'customers',
          label: t('shell.nav.customers'),
          items: customers.items.map((c) => ({
            id: c.id,
            identifier: c.customerNumber,
            label: customerName(c),
            sublabel: c.address ? `${c.address.postalCode} ${c.address.locality}` : undefined,
            href: `/customers/${c.id}`,
          })),
        },
        {
          key: 'vehicles',
          label: t('shell.nav.vehicles'),
          // `resolved`/`pickerCandidates` serve the dedicated identifier-
          // resolution UX on the vehicle list itself (FR-V-06/16) — global
          // search only ever shows the ordinary filtered page.
          items: vehicles.filtered.items.slice(0, GLOBAL_SEARCH_LIMIT_PER_ENTITY).map((v) => ({
            id: v.id,
            identifier: v.vin,
            label: v.vehicleNumber,
            href: `/vehicles/${v.id}`,
          })),
        },
      ]
      return groups.filter((group) => group.items.length > 0)
    },
    onSelect: (item) => navigate(item.href),
  }
}

function BrandMark() {
  return (
    <div
      aria-hidden="true"
      style={{
        width: 28,
        height: 28,
        borderRadius: 8,
        backgroundColor: purple[6],
        color: white,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 14,
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      N
    </div>
  )
}

/** The application shell wired up for the DMS app specifically — nav
 * structure, auth, and preference persistence live here; the shell
 * components themselves (@nexotec/ui-kit) know nothing about any of it.
 *
 * Wraps in UiPreferencesProvider so the `ui` preference scope (sidebar
 * state, language, density) is fetched/held once and shared with whatever
 * page renders inside — e.g. the customer grid's density toggle reads
 * the same instance this shell's sidebar collapse does.
 */
export function DmsShell({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (!user) return <>{children}</>

  return (
    <UiPreferencesProvider>
      <DmsShellInner>{children}</DmsShellInner>
    </UiPreferencesProvider>
  )
}

function DmsShellInner({ children }: { children: ReactNode }) {
  const { user, activeDealership, memberships, logout, switchDealership } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { t, i18n } = useTranslation()
  const { sidebarCollapsed, uiLanguage, setSidebarCollapsed, setUiLanguage } = useUiPreferencesContext()

  const activeHref = '/' + (location.pathname.split('/')[1] ?? '')

  // FR-13: "UI language is switchable at any time" and persisted on the
  // user profile — the preference already persists (useUiPreferences);
  // this is what makes the switch actually retranslate the app instead of
  // just updating stored state. Its control lives in the sidebar's account
  // cluster, not the top bar (revised 2026-08-16 — see Sidebar's docstring).
  useEffect(() => {
    void i18n.changeLanguage(uiLanguage)
  }, [uiLanguage, i18n])

  if (!user) return <>{children}</>

  return (
    <AppShell
      collapsed={sidebarCollapsed}
      onToggleCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)}
      sidebar={{
        brand: <BrandMark />,
        productName: 'Nexotec',
        moduleSubtitle: 'DMS',
        groups: buildNavGroups(t),
        activeHref,
        user: { name: `${user.firstName} ${user.lastName}`, email: user.email, role: user.role },
        uiLanguage,
        onLanguageChange: setUiLanguage,
        onSignOut: () => {
          logout().then(() => navigate('/login'))
        },
        signOutLabel: t('shell.signOut'),
        activeDealership: activeDealership ?? undefined,
        memberships,
        onSwitchDealership: (dealershipId) => {
          void switchDealership(dealershipId)
        },
        linkComponent: Link,
      }}
      topbar={{ search: buildGlobalSearch(t, navigate) }}
    >
      {/* § ADR-059 — mounted once, here, so any screen in the app can open
          an overlay via useOverlay() without knowing where the stack
          itself lives. Inside AppShell (not wrapping it) so overlaid
          content still renders within the same BreadcrumbProvider —
          useSetBreadcrumb(null) on the embedded side is what stops that
          from being a problem, not moving the provider around it. */}
      <OverlayProvider>{children}</OverlayProvider>
    </AppShell>
  )
}
