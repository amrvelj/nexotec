// @vitest-environment jsdom
import type { ReactElement } from 'react'
import { afterAll, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { waitFor } from '@testing-library/react'
import i18n, { SUPPORTED_LANGUAGES } from './index'
import { DmsShell } from '../layout/DmsShell'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { collectMissingKeys } from '../test/i18nScan'
import { customer, customerPage } from '../test/fixtures'
import { CustomersListPage } from '../pages/CustomersListPage'
import { CustomerCreatePage } from '../pages/CustomerCreatePage'
import { CustomerDetailPage } from '../pages/CustomerDetailPage'
import { DashboardPage } from '../pages/DashboardPage'
import { VehiclesListPage } from '../pages/VehiclesListPage'
import { StockListPage } from '../pages/StockListPage'
import { StockCreatePage } from '../pages/StockCreatePage'
import { SalesListPage } from '../pages/SalesListPage'
import { ValuationsListPage } from '../pages/ValuationsListPage'
import { ValuationCreatePage } from '../pages/ValuationCreatePage'
import { ValuationDetailPage } from '../pages/ValuationDetailPage'
import { IntegrationsPage } from '../pages/IntegrationsPage'
import { MappingGapsPage } from '../pages/MappingGapsPage'
import { SignInErrorPage } from '../pages/SignInErrorPage'

// WP-6c: "zero missing translation keys in all four languages, proven by a
// route-walking test." localeKeyParity.test.ts only compares the four
// bundles against each other, so a key missing from ALL four still passes
// it. This walks the real routes in every language and fails on the loud
// `⚠ MISSING I18N KEY` marker i18n/index.ts renders for such a key —
// scanning rendered text AND attributes (aria-label / placeholder / title).
//
// LoginPage is out: it has no `t()` calls (Zitadel hosts the real sign-in)
// and mounting it only fires an OAuth redirect.

interface Screen {
  name: string
  /** MemoryRouter entry. */
  path: string
  /** Route pattern when the path carries params. Defaults to `path`. */
  routePath?: string
  element: ReactElement
  /** Screen-specific happy-path data. Anything unmatched falls through to
   * the benign fallback, so a screen still renders its empty or error
   * state (both translated) even with no fixture. */
  routes?: FakeRoute[]
  /** Rendered without the shell (the sign-in error screen). */
  bare?: boolean
}

const customerDetailRoutes: FakeRoute[] = [
  { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
  { match: /^\/customers\/c1\/(phones|emails)$/, handler: () => ({ items: [] }) },
  { match: /^\/customers\/c1\/(vehicles|external-ids)$/, handler: () => ({ items: [], nextCursor: null }) },
  { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
]

const valuationDetailRoutes: FakeRoute[] = [
  {
    match: /^\/valuations\/v1$/,
    handler: () => ({
      id: 'v1',
      valuationNumber: 'BW-2026-0001',
      status: 'valid',
      source: 'auto_i_dat',
      vehicleMake: 'VW',
      vehicleModel: 'Golf',
      vehicleTrim: null,
      vehicleVin: 'WVWZZZ1KZAW000000',
      vehiclePlate: null,
      vehicleId: null,
      vehicleFirstRegistration: null,
      customerId: null,
      customerLabel: null,
      mileage: 42000,
      providerValue: '18000.00',
      deductions: [],
      finalOffer: '17000.00',
      note: null,
      validUntil: '2026-12-31',
      version: 1,
      createdAt: '2026-02-01T00:00:00Z',
      updatedAt: '2026-02-01T00:00:00Z',
    }),
  },
]

const SCREENS: Screen[] = [
  { name: 'sign-in-error', path: '/sign-in-error', element: <SignInErrorPage />, bare: true },
  { name: 'dashboard', path: '/', element: <DashboardPage /> },
  {
    name: 'customers list',
    path: '/customers',
    element: <CustomersListPage />,
    routes: [{ match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1' })]) }],
  },
  { name: 'customer create', path: '/customers/new', element: <CustomerCreatePage /> },
  { name: 'customer detail', path: '/customers/c1', routePath: '/customers/:id', element: <CustomerDetailPage />, routes: customerDetailRoutes },
  {
    name: 'vehicles list',
    path: '/vehicles',
    element: <VehiclesListPage />,
    routes: [
      { match: /^\/vehicle-mdm\/search$/, handler: () => ({ resolved: null, pickerCandidates: [], filtered: { items: [], nextCursor: null } }) },
    ],
  },
  { name: 'stock list', path: '/stock', element: <StockListPage /> },
  { name: 'stock create', path: '/stock/new', element: <StockCreatePage /> },
  { name: 'sales list', path: '/sales', element: <SalesListPage /> },
  { name: 'valuations list', path: '/valuations', element: <ValuationsListPage /> },
  { name: 'valuation create', path: '/valuations/new', element: <ValuationCreatePage /> },
  { name: 'valuation detail', path: '/valuations/v1', routePath: '/valuations/:id', element: <ValuationDetailPage />, routes: valuationDetailRoutes },
  { name: 'integrations', path: '/integrations', element: <IntegrationsPage /> },
  { name: 'mapping gaps', path: '/vehicle-mdm/mapping-gaps', element: <MappingGapsPage /> },
]

const BENIGN_FALLBACK = () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, payload: {} })

function uiPrefRoute(lang: string): FakeRoute {
  return {
    method: 'GET',
    match: /\/me\/preferences\/ui$/,
    handler: () => ({ payload: { schemaVersion: 1, uiLanguage: lang, sidebarCollapsed: false, density: 'default' } }),
  }
}

async function renderScreen(scr: Screen, lang: string) {
  installFakeBackend([uiPrefRoute(lang), ...(scr.routes ?? [])], { fallback: BENIGN_FALLBACK })
  await i18n.changeLanguage(lang)

  const routed = (
    <Routes>
      <Route path={scr.routePath ?? scr.path} element={scr.element} />
    </Routes>
  )
  renderWithProviders(scr.bare ? routed : <DmsShell>{routed}</DmsShell>, { route: scr.path })

  if (!scr.bare) {
    // Let queries settle so error/empty/content states (all translated)
    // have rendered before the scan.
    await waitFor(() => expect(document.querySelectorAll('.mantine-Loader-root')).toHaveLength(0), { timeout: 3000 })
  }
  await new Promise((r) => setTimeout(r, 20))
}

afterAll(async () => {
  // Don't leave a later test file in another language.
  await i18n.changeLanguage('de')
})

describe('every route renders with zero missing i18n keys, in all four languages', () => {
  it('walks all four supported languages', () => {
    expect(SUPPORTED_LANGUAGES).toEqual(['de', 'fr', 'it', 'en'])
  })

  for (const lang of SUPPORTED_LANGUAGES) {
    describe(lang, () => {
      it.each(SCREENS.map((s) => s.name))('%s', async (name) => {
        const scr = SCREENS.find((s) => s.name === name)!
        await renderScreen(scr, lang)
        const missing = collectMissingKeys()
        expect(missing, `${name} [${lang}] rendered missing keys: ${missing.join(', ')}`).toEqual([])
      })
    })
  }
})
