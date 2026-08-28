import { useEffect, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle,
  ArrowLeftRight,
  Car,
  ChartColumn,
  Cog,
  Handshake,
  Receipt,
  ShieldCheck,
  Store,
  Users,
  Warehouse,
  Wrench,
} from 'lucide-react'
import { AppShell, purple, type NavGroupConfig } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { UiPreferencesProvider, useUiPreferencesContext } from '../hooks/UiPreferencesContext'

// Translated at render time (not a module-level constant) — labels must
// re-render when the user switches UI language via the top bar.
function buildNavGroups(t: (key: string) => string): NavGroupConfig[] {
  return [
    {
      label: t('shell.nav.masterData'),
      items: [
        { label: t('shell.nav.customers'), href: '/customers', icon: Users, status: 'active' },
        { label: t('shell.nav.vehicles'), href: '/vehicles', icon: Car, status: 'soon' },
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
        { label: t('shell.nav.sales'), href: '/sales', icon: Handshake, status: 'soon' },
        { label: t('shell.nav.aftersales'), href: '/aftersales', icon: Wrench, status: 'soon' },
        { label: t('shell.nav.inventory'), href: '/inventory', icon: Warehouse, status: 'soon' },
        { label: t('shell.nav.parts'), href: '/parts', icon: Cog, status: 'soon' },
        { label: t('shell.nav.finance'), href: '/finance', icon: Receipt, status: 'soon' },
        { label: t('shell.nav.transactions'), href: '/transactions', icon: ArrowLeftRight, status: 'soon' },
        { label: t('shell.nav.reporting'), href: '/reporting', icon: ChartColumn, status: 'soon' },
        { label: t('shell.nav.compliance'), href: '/compliance', icon: ShieldCheck, status: 'soon' },
      ],
    },
  ]
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
        color: '#fff',
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
      topbar={{}}
    >
      {children}
    </AppShell>
  )
}
