import { useEffect, useState, type ReactNode } from 'react'
import { Alert } from '@mantine/core'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import {
  Archive,
  Building2,
  Car,
  CarFront,
  ChartColumn,
  Cog,
  Database,
  Globe,
  FileArchive,
  FileText,
  Handshake,
  Hash,
  Languages,
  LayoutDashboard,
  LifeBuoy,
  MapPin,
  Palette,
  FileImage,
  Plug,
  Receipt,
  ShieldCheck,
  Store,
  Tags,
  UserCog,
  Users,
  Warehouse,
  Wrench,
} from 'lucide-react'
import { AppShell, EnvironmentNotice, OverlayProvider, purple, white, type GlobalSearchGroup, type GlobalSearchProps, type NavGroupConfig } from '@nexotec/ui-kit'
import { useAuth } from '../auth/AuthContext'
import { outboxWorkerAbsent } from '../config/deployment'
import { LEGAL_PRIVACY_PATH, LEGAL_TERMS_PATH } from '../config/legalPages'
import { UiPreferencesProvider, useUiPreferencesContext } from '../hooks/UiPreferencesContext'
import { api, ApiError } from '../api/client'
import { customerName } from '../shared/displayNames'
import type { CustomerPage, VehicleSearchResult } from '../api/types'
import { filterNavGroupsByEntitlements } from './navEntitlements'
import { useUnreadNotificationCount } from '../hooks/useNotifications'
import { rebindPushToThisSession } from '../utils/webPush'

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
        // C-B (KAN-40 / FR-C-01) — catalogue browse + the mapping-gap queue,
        // matching the prototype's own `#/catalogue` Master-data entry.
        { label: t('shell.nav.catalogue'), href: '/catalogue', icon: Database, status: 'active' },
        { label: t('shell.nav.partners'), href: '/partners', icon: Store, status: 'soon' },
        // WP-6 PR-7 — server-side enforcement (require_read /
        // require_access_role) is the actual control; the nav item is
        // never role-hidden (Risk R-4's own posture).
        { label: t('shell.nav.integrations'), href: '/integrations', icon: Plug, status: 'active' },
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
    {
      // The prototype's "Verwaltung / Administration" group. Reference-data
      // admin is platform_admin-only in practice (the API 403s otherwise);
      // the nav entry is never role-hidden — server-side enforcement is the
      // control, not the UI (Risk R-4). The mapping-gap queue that used to
      // have its own entry is now a section of this screen.
      label: t('shell.nav.administration'),
      items: [
        // KAN-200 — Dealer Administration › Users. Manager-only at the API
        // (`dealership_users`); the entry is never role-hidden (A-RULE-7).
        // KAN-203 — Dealer Administration › Business (A-RULE-0: the one place
        // the Betrieb's master data is configured). Any role reads it; the
        // writes are the manager's at the API, the entry is never role-hidden.
        { label: t('shell.nav.business'), href: '/administration/business', icon: Building2, status: 'active' },
        // KAN-204 — Dealer Administration › Locations. Any role reads; the manager
        // writes at the API (`locations`); the entry is never role-hidden.
        { label: t('shell.nav.locations'), href: '/administration/locations', icon: MapPin, status: 'active' },
        // KAN-205 — Dealer Administration › Logo and colours (PRD "Logo and
        // brand assets"). Any role reads, the manager writes; never role-hidden.
        { label: t('shell.nav.branding'), href: '/administration/branding', icon: Palette, status: 'active' },
        // KAN-206 — Dealer Administration › Letterhead and template (PRD «Branding and
        // documents»). Any role reads; the manager writes and previews. Never role-hidden.
        { label: t('shell.nav.letterhead'), href: '/administration/letterhead', icon: FileImage, status: 'active' },
        { label: t('shell.nav.users'), href: '/administration/users', icon: UserCog, status: 'active' },
        // KAN-208 — Dealer Administration › Standard texts. Any role reads; a
        // category is written by the manager or its delegate (FR-A-28) — the
        // entry is never role-hidden (A-RULE-7).
        { label: t('shell.nav.standardTexts'), href: '/administration/texts', icon: FileText, status: 'active' },
        // KAN-207 — Dealer Administration › Number ranges. Any role reads; the
        // manager edits an open range, platform staff a frozen one (A-RULE-3).
        // Never role-hidden (A-RULE-7).
        { label: t('shell.nav.numberRanges'), href: '/administration/number-ranges', icon: Hash, status: 'active' },
        // KAN-466 — Dealer Administration › Contact groups. Any role reads; the
        // manager switches a group on or off (A-RULE-0). Never role-hidden (A-RULE-7).
        { label: t('shell.nav.contactGroups'), href: '/administration/contact-groups', icon: Tags, status: 'active' },
        // KAN-218 — Dealer Administration › Data export (D-A-31, FR-A-44). The
        // manager's at the API (`data_export`); never role-hidden (A-RULE-7).
        { label: t('shell.nav.dataExport'), href: '/administration/export', icon: FileArchive, status: 'active' },
        // KAN-202 — platform staff's account recovery (FR-AU-25…27). Staff-only
        // at the API; the entry is never role-hidden (Anto, 2026-10-22: under
        // Administration, like every other admin entry — A-RULE-7).
        { label: t('shell.nav.accountRecovery'), href: '/administration/account-recovery', icon: LifeBuoy, status: 'active' },
        { label: t('shell.nav.referenceData'), href: '/settings/reference', icon: Languages, status: 'active' },
        // KAN-209 — Platform Administration › Translations (D-A-23). Platform staff
        // write at the API; anyone reads. Never role-hidden (A-RULE-7).
        { label: t('shell.nav.translations'), href: '/platform/translations', icon: Globe, status: 'active' },
        // KAN-210 — Platform Administration › Onboarding. Staff-only at the API;
        // never role-hidden (A-RULE-7).
        { label: t('shell.nav.onboarding'), href: '/platform/onboarding', icon: Building2, status: 'active' },
        // KAN-218 — Platform Administration › Offboarding (FR-A-45). Staff-only
        // at the API; never role-hidden (A-RULE-7).
        { label: t('shell.nav.offboarding'), href: '/platform/offboarding', icon: Archive, status: 'active' },
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
      const customerGroup: GlobalSearchGroup = {
        key: 'customers',
        label: t('shell.nav.customers'),
        items: customers.items.map((c) => ({
          id: c.id,
          identifier: c.customerNumber,
          label: customerName(c),
          sublabel: c.address ? `${c.address.addressPostalCode} ${c.address.addressLocality}` : undefined,
          href: `/customers/${c.id}`,
        })),
      }
      const vehicleGroup = buildVehicleGroup(t, vehicles)
      // § FR-UI-08 — identifiers first, best group first: a plate or VIN
      // that resolved is the exact answer, so its group leads.
      const groups = vehicleGroup.isIdentifierAnswer ? [vehicleGroup.group, customerGroup] : [customerGroup, vehicleGroup.group]
      return groups.filter((group) => group.items.length > 0)
    },
    onSelect: (item) => navigate(item.href),
  }
}

/**
 * KAN-82 — global search is FR-V-06's second entry point: "a plate typed
 * here resolves through the same rules as FR-V-06, ambiguity picker
 * included; global search never guesses either" (FR-UI-08). The server
 * decides from the string's shape whether it is an identifier, so this
 * reads its answer rather than guessing again: the resolved vehicle, or
 * the picker's candidates under the same Wechselschild / conflict
 * headings the Vehicles screen shows, or — for anything else — the
 * ordinary filtered page — which, for an identifier that matches nothing,
 * is filtered by that identifier and so empty: it reads as no match, never
 * as other cars.
 */
function buildVehicleGroup(
  t: (key: string) => string,
  result: VehicleSearchResult,
): { group: GlobalSearchGroup; isIdentifierAnswer: boolean } {
  if (result.resolved) {
    const v = result.resolved
    return {
      isIdentifierAnswer: true,
      group: {
        key: 'vehicles',
        label: t('shell.nav.vehicles'),
        items: [{ id: v.id, identifier: v.vin, label: v.vehicleNumber, sublabel: v.currentPlateWithheld ? t('vehicleDetail.plates.withheld') : (v.currentPlate ?? undefined), href: `/vehicles/${v.id}` }],
      },
    }
  }
  if (result.pickerCandidates.length > 0) {
    const isWechselschild = result.pickerCandidates.every((c) => !c.isConflict)
    return {
      isIdentifierAnswer: true,
      group: {
        key: 'vehicles',
        label: isWechselschild ? t('vehiclesList.picker.wechselschildTitle') : t('vehiclesList.picker.conflictTitle'),
        items: result.pickerCandidates.map((c) => ({
          id: c.id,
          identifier: c.vin,
          label: c.vehicleNumber,
          sublabel: c.plate ?? undefined,
          href: `/vehicles/${c.id}`,
        })),
      },
    }
  }
  return {
    isIdentifierAnswer: false,
    group: {
      key: 'vehicles',
      label: t('shell.nav.vehicles'),
      items: result.filtered.items.slice(0, GLOBAL_SEARCH_LIMIT_PER_ENTITY).map((v) => ({
        id: v.id,
        identifier: v.vin,
        label: v.vehicleNumber,
        href: `/vehicles/${v.id}`,
      })),
    },
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
  const { user, activeDealership, memberships, entitlements, logout, switchDealership, mfaEnrolmentRequired } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { t, i18n } = useTranslation()
  const { sidebarCollapsed, uiLanguage, setSidebarCollapsed, setUiLanguage } = useUiPreferencesContext()
  const [switchError, setSwitchError] = useState<string | null>(null)

  const navGroups = filterNavGroupsByEntitlements(buildNavGroups(t), entitlements)

  // The sidebar matches `item.href` exactly. Most nav routes are a single
  // segment (`/customers`, `/stock`), but a couple are two
  // (`/settings/reference`) — take the longest nav href that prefixes the
  // current path so those still light up.
  const activeHref =
    navGroups
      .flatMap((group) => group.items)
      .map((item) => item.href)
      .filter((href) => location.pathname === href || location.pathname.startsWith(href + '/'))
      .sort((a, b) => b.length - a.length)[0] ?? '/' + (location.pathname.split('/')[1] ?? '')

  // FR-13: "UI language is switchable at any time" and persisted on the
  // user profile — the preference already persists (useUiPreferences);
  // this is what makes the switch actually retranslate the app instead of
  // just updating stored state. Its control lives in the sidebar's account
  // cluster, not the top bar (revised 2026-08-16 — see Sidebar's docstring).
  useEffect(() => {
    void i18n.changeLanguage(uiLanguage)
  }, [uiLanguage, i18n])

  // KAN-216 (D-A-40): the bell. A session that still owes a second factor
  // reaches no inbox (the API confines it to enrolment), so it shows none.
  const inboxReachable = !mfaEnrolmentRequired
  const unread = useUnreadNotificationCount(inboxReachable)
  const unreadCount = unread.data?.count ?? 0
  // A browser that already pushes binds its subscription to this new
  // session (FR-A-49: the previous session's went with it).
  useEffect(() => {
    if (inboxReachable) void rebindPushToThisSession()
  }, [inboxReachable, user?.id])

  // KAN-401 (Anto, 2026-10-30, option 2): the session record says whether the
  // user has a photo; the account card shows it. The same query key as
  // `UserAvatar` on My account, so the two never fetch the one photo twice,
  // and the record's version in it, so a new photo replaces the old one.
  const ownPhoto = useQuery({
    queryKey: ['user-photo', '/me/photo', user?.version],
    queryFn: () => api.getBlobUrl('/me/photo'),
    enabled: !!user?.hasPhoto && !mfaEnrolmentRequired,
    staleTime: Infinity,
  })

  if (!user) return <>{children}</>

  return (
    <AppShell
      collapsed={sidebarCollapsed}
      onToggleCollapsed={() => setSidebarCollapsed(!sidebarCollapsed)}
      sidebar={{
        brand: <BrandMark />,
        productName: 'Nexotec',
        moduleSubtitle: 'DMS',
        groups: navGroups,
        activeHref,
        // KAN-163 — `user.role` is the UserRole enum code (`gm`,
        // `service_advisor`); the sidebar shows its translated job title.
        user: {
          name: `${user.firstName} ${user.lastName}`,
          email: user.email,
          role: t(`userRole.${user.role}`),
          photoUrl: user.hasPhoto ? (ownPhoto.data ?? null) : null,
        },
        uiLanguage,
        onLanguageChange: setUiLanguage,
        onSignOut: () => {
          logout().then(() => navigate('/login'))
        },
        onAccountSecurity: () => navigate('/account/profile'),
        labels: {
          mainNavigation: t('shell.sidebar.mainNavigation'),
          expandSidebar: t('shell.sidebar.expandSidebar'),
          collapseSidebar: t('shell.sidebar.collapseSidebar'),
          soon: t('shell.sidebar.soon'),
          notifications: t('shell.sidebar.notifications'),
          notificationsSoon: t('shell.sidebar.notificationsSoon'),
          accountMenu: t('shell.sidebar.accountMenu'),
          language: t('shell.sidebar.language'),
          switchDealership: t('shell.sidebar.switchDealership'),
          signOut: t('shell.signOut'),
          accountSecurity: t('shell.sidebar.accountSecurity'),
        },
        activeDealership: activeDealership ?? undefined,
        memberships,
        notifications: inboxReachable
          ? {
              unreadCount,
              onOpen: () => navigate('/notifications'),
              ariaLabel: t('notifications.bellLabel', { count: unreadCount }),
              active: location.pathname === '/notifications',
            }
          : undefined,
        onSwitchDealership: (dealershipId) => {
          setSwitchError(null)
          // KAN-237 — a refused switch is said, in the UI language, and
          // the active dealership stays as it was. A 401 has already ended
          // the session (AuthProvider) and routes to the login screen, so
          // it gets no message here.
          switchDealership(dealershipId).catch((err: unknown) => {
            if (err instanceof ApiError && err.status === 401) return
            setSwitchError(
              err instanceof ApiError && err.status === 403
                ? t('shell.switchDealershipError.notAMember')
                : t('shell.switchDealershipError.failed')
            )
          })
        },
        // KAN-452 — the public Terms of Use and Privacy Policy, at the foot
        // of the account menu (the shell has no footer of its own; account
        // chrome lives in the sidebar's foot). They open in a new tab, so
        // the screen underneath — a half-built offer — is never lost.
        footerLinks: [
          { label: t('legal.termsLink'), href: LEGAL_TERMS_PATH },
          { label: t('legal.privacyLink'), href: LEGAL_PRIVACY_PATH },
        ],
        linkComponent: Link,
      }}
      // KAN-104 item 1 (FR-UI-07): the dashboard is the breadcrumb's root,
      // a link on every other screen.
      topbar={{
        search: buildGlobalSearch(t, navigate),
        breadcrumbRoot: { label: t('shell.nav.dashboard'), href: '/', icon: <LayoutDashboard size={14} /> },
        linkComponent: Link,
      }}
      // KAN-275 — ADR-081 (option D): staging runs no outbox worker, so no
      // event consumer and no nightly job runs there. Say so on every
      // signed-in screen (ADR-084) rather than let a tester read a missing
      // pipeline item as a bug. Build-time setting, never the environment's
      // name.
      notice={
        outboxWorkerAbsent() ? (
          <EnvironmentNotice>{t('shell.environmentNotice.outboxWorkerAbsent')}</EnvironmentNotice>
        ) : undefined
      }
    >
      {/* § ADR-059 — mounted once, here, so any screen in the app can open
          an overlay via useOverlay() without knowing where the stack
          itself lives. Inside AppShell (not wrapping it) so overlaid
          content still renders within the same BreadcrumbProvider —
          useSetBreadcrumb(null) on the embedded side is what stops that
          from being a problem, not moving the provider around it. */}
      <OverlayProvider closeLabel={t('common.overlay.close')}>
        {/* KAN-237 — the DMS's refused-action pattern: an inline red
            alert above the content, dismissable, never the ADR-084
            notice strip (that slot is for deployment-level notices). */}
        {switchError && (
          <Alert
            color="red"
            role="alert"
            mb="md"
            withCloseButton
            closeButtonLabel={t('shell.switchDealershipError.dismiss')}
            onClose={() => setSwitchError(null)}
          >
            {switchError}
          </Alert>
        )}
        {children}
      </OverlayProvider>
    </AppShell>
  )
}
