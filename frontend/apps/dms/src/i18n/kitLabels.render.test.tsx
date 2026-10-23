// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useOverlay } from '@nexotec/ui-kit'
import i18n from './index'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend } from '../test/fakeBackend'
import type { StockItemPage } from '../api/types'
import { ContactPointsEditor } from '../components/customer-detail/ContactPointsEditor'
import { DmsShell } from '../layout/DmsShell'
import { StockListPage } from '../pages/StockListPage'

// KAN-364 — four kit components merged English defaults under optional
// labels, and no screen passed the views-and-filters panel any: every list
// showed "Views", "Manage filters", "+ New filter…", "Save" and "Cancel" in
// every UI language, and the filter and view row actions and the overlay's
// close button were English names no caller could change. The labels are
// required now and every caller passes translated keys. The Filter Builder's
// condition names and a saved filter's text (KAN-415), the save-view confirm
// button's name (KAN-416) and the Action Bar's controls (KAN-364 part 2) are
// keys too.
//
// Hardcoded French expectations, not i18n.t(): a key missing from both sides
// must fail here.

// jsdom has no layout, so TanStack Virtual would window down to zero rows.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

afterEach(async () => {
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

const ENGLISH = /^(\+ New filter…|New filter…|Views|Manage filters|Save current view as…|Reset|Delete view|Edit filter|Duplicate filter|Delete filter|Save|Cancel|Close|Primary|No longer valid|Does not work|Consent|Save view|contains|is exactly|is not|is|equals|greater than|less than|is today|is this week|is this month|is this year|is in the last N days|is more than N days ago|.* (contains|is exactly|is not|is|equals) .*|Not .*|Refresh|Change row density)$/
function englishOnScreen(): string[] {
  const found: string[] = []
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim() ?? ''
    if (ENGLISH.test(text)) found.push(text)
  }
  document.body.querySelectorAll('[aria-label], [placeholder]').forEach((el) => {
    for (const value of [el.getAttribute('aria-label'), el.getAttribute('placeholder')]) {
      if (value && ENGLISH.test(value)) found.push(value)
    }
  })
  return found
}

const NOW = '2026-10-01T09:00:00Z'
const STOCK_PAGE = {
  items: [{
    id: 's1', stockNumber: 'ST-3640', vehicleLabel: 'VW Golf GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: null,
    condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
    odometerKm: 12500, listPrice: '42500.00', effectivePrice: '42500.00', basePrice: null, bodyStyle: null,
    exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
    landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
    notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
    pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null, purchasePrice: null,
    supplierIsVatRegistered: null, supplierName: null, valuationRefAmount: null, valuationRefId: null,
    valuationRefSource: null, valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 1,
  }],
  nextCursor: null, total: 1, totalIsEstimate: false,
} as unknown as StockItemPage

describe('kit components speak the UI language on the app\'s screens (KAN-364)', () => {
  it('French UI: the Stock list\'s views-and-filters panel and its filter editor show their labels in French', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([{ match: /^\/inventory\/stock-items$/, handler: () => STOCK_PAGE }])
    renderWithProviders(<StockListPage />, { route: '/stock' })
    await screen.findByText('ST-3640')

    await user.click(screen.getByRole('button', { name: /Tous les véhicules/ }))
    expect(await screen.findByText('Vues')).toBeInTheDocument()
    expect(screen.getByText('Gérer les filtres')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer la vue actuelle sous…' })).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Actualiser' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Modifier la densité des lignes' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer la vue actuelle sous…' }))
    expect(screen.getByRole('button', { name: 'Enregistrer la vue' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Nouveau filtre…' }))
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annuler' })).toBeInTheDocument()
    expect(screen.getAllByRole('option', { name: 'est' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('option', { name: "n'est pas" }).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    expect(await screen.findByRole('button', { name: 'Modifier le filtre' })).toBeInTheDocument()
    // KAN-415: the saved filter reads as a French sentence, not "… is Pipeline".
    expect(screen.getByText('Statut de stock est Pipeline')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dupliquer le filtre' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Supprimer le filtre' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réinitialiser' })).toBeInTheDocument()

    expect(englishOnScreen()).toEqual([])
  })

  it('French UI: a customer\'s phone rows, their menu and the add row are French', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([])
    const label = i18n.t('customerDetail.contactPoints.phoneNumbers')
    renderWithProviders(
      <ContactPointsEditor<'mobile'>
        label={label}
        addLabel={i18n.t('customerDetail.contactPoints.addPhone')}
        typeOptions={[{ value: 'mobile', label: 'Mobile' }]}
        rows={[{ id: 'p1', type: 'mobile', value: '+41791112233', isPrimary: true, consentGranted: false }]}
        newRowType="mobile"
        renderValueEditor={(value) => <span>{value}</span>}
        onCreate={async () => {}}
        onUpdate={async () => {}}
        onDelete={async () => {}}
      />,
    )

    expect(screen.getByText('Consentement')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: `Actions de la ligne ${label}` }))
    expect(await screen.findByText("N'est plus valable")).toBeInTheDocument()
    expect(screen.getByText('Ne fonctionne pas')).toBeInTheDocument()
    expect(englishOnScreen()).toEqual([])
  })

  it('French UI: the overlay DmsShell mounts names its close button in French', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'GET',
        match: /\/me\/preferences\/ui$/,
        handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage: 'fr', density: 'default' } }),
      },
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: {
            id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
            phone: null, role: 'service_advisor', accessRoles: ['sales'], isDealerManager: false,
            employmentStatus: 'active', authIdentityId: 'auth-1', status: 'active', version: 1,
            createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
          },
          activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
          memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }], entitlements: ALL_ENTITLEMENTS,
        }),
      },
    ])
    function OpenCustomer() {
      const overlay = useOverlay()
      return (
        <button type="button" onClick={() => overlay.push({ key: 'c1', content: <p>Fiche client</p> })}>
          ouvrir
        </button>
      )
    }
    renderWithProviders(
      <DmsShell>
        <OpenCustomer />
      </DmsShell>,
    )
    await screen.findByRole('navigation', { name: i18n.getFixedT('fr')('shell.sidebar.mainNavigation') })

    await user.click(screen.getByRole('button', { name: 'ouvrir' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Fiche client')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Fermer' }))
    expect(screen.queryByText('Fiche client')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })
})
