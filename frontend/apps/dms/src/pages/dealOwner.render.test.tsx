// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import type { SalesContractRead, SalesOfferRead } from '../api/types'
import { ContractDetailPage } from './ContractDetailPage'
import { OfferDetailPage } from './OfferDetailPage'
import { OfferWorkspacePage } from './OfferWorkspacePage'
import { SalesListPage } from './SalesListPage'

// KAN-405 — the responsible seller (KAN-200's `ownerUserId`) is set on the
// offer workspace, a sent offer and a pending contract through a picker of
// the Betrieb's sellers, sent as `ownerUserId` with If-Match; «Übernehmen»
// is one action shared by the Sales list's row menu and the detail
// overflows (ADR-061). Anto's ruling 2026-10-28 (option A): a sent offer
// takes a new seller too, so every row of the unassigned queue can be taken.

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

const SELLERS: FakeRoute = {
  match: /^\/customers\/advisor-options$/,
  handler: () => ({ items: [{ id: 'user-1', label: 'Test Advisor' }, { id: 'u-2', label: 'Kim Koller' }] }),
}

const deal = (overrides: Record<string, unknown>) => ({
  id: 'd-1',
  entityType: 'offer',
  number: 'O-2026-0009',
  status: 'open',
  offerId: 'o-9',
  offerNumber: 'O-2026-0009',
  contractId: null,
  contractNumber: null,
  customerId: null,
  customerLabel: 'Ursula Vogt',
  customerLocality: null,
  vehicleLabel: 'VW Golf',
  ownerUserId: null,
  ownerLabel: null,
  grossPrice: '25000.00',
  margin: null,
  documentsCount: 1,
  createdAt: '2026-10-01T10:00:00Z',
  updatedAt: '2026-10-02T10:00:00Z',
  ...overrides,
})

const page = (items: unknown[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

const CONTRACT = {
  id: 'k1',
  contractNumber: 'V-2026-0001',
  offerNumber: null,
  status: 'pending',
  version: 3,
  customerId: null,
  customerLabel: null,
  vehicleSource: null,
  vehicleLabel: null,
  grossPrice: null,
  tradeInValue: null,
  payable: null,
  margin: null,
  financing: null,
  vehicleSnapshotFrozenAt: null,
  ownerUserId: null,
  ownerLabel: null,
  containers: [],
} as unknown as SalesContractRead

const OPEN_OFFER = {
  id: 'o-9',
  offerNumber: 'O-2026-0009',
  status: 'open',
  vehicleLabel: 'VW Golf',
  version: 4,
  ownerUserId: 'u-2',
  ownerLabel: 'Kim Koller',
} as unknown as SalesOfferRead

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('Sales list — «Übernehmen» in the row menu (KAN-405)', () => {
  it('takes an unassigned sent offer for the signed-in user with the version it reads', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/sales\/deals$/, handler: () => page([deal({})]) },
      { method: 'GET', match: /^\/sales\/offers\/o-9$/, handler: () => ({ ...OPEN_OFFER, ownerUserId: null, ownerLabel: null }) },
      { method: 'PATCH', match: /^\/sales\/offers\/o-9$/, handler: () => ({ ...OPEN_OFFER, ownerUserId: 'user-1', ownerLabel: 'Test Advisor', version: 5 }) },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })

    await screen.findByText('Ursula Vogt')
    await user.click(screen.getByRole('button', { name: 'Zeilenaktionen' }))
    const takeOver = await screen.findByRole('menuitem', { name: /Übernehmen/ })
    expect(takeOver).toBeEnabled()
    await user.click(takeOver)

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')
    expect(patch.body).toEqual({ ownerUserId: 'user-1' })
    expect(patch.headers.get('If-Match')).toBe('4')
  })

  it('never overwrites a colleague who took the deal after the list loaded', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/sales\/deals$/, handler: () => page([deal({})]) },
      // The row still reads unassigned; the offer itself has a seller by now.
      { method: 'GET', match: /^\/sales\/offers\/o-9$/, handler: () => OPEN_OFFER },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })

    await screen.findByText('Ursula Vogt')
    await user.click(screen.getByRole('button', { name: 'Zeilenaktionen' }))
    await user.click(await screen.findByRole('menuitem', { name: /Übernehmen/ }))

    expect(await screen.findByText(i18n.t('dealOwner.errors.takenMeanwhile', { owner: 'Kim Koller' }))).toBeInTheDocument()
    expect(backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')).toHaveLength(0)
  })

  it('shows «Übernehmen» disabled, with the reason, on a deal that has a seller or is closed', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        match: /^\/sales\/deals$/,
        handler: () =>
          page([
            deal({ ownerUserId: 'u-2', ownerLabel: 'Kim Koller' }),
            deal({ id: 'd-2', number: 'O-2026-0010', offerId: 'o-10', status: 'cancelled', customerLabel: 'Beat Huber' }),
          ]),
      },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })

    await screen.findByText('Ursula Vogt')
    const [first, second] = screen.getAllByRole('button', { name: 'Zeilenaktionen' })
    await user.click(first)
    const assigned = await screen.findByRole('menuitem', { name: /Übernehmen/ })
    expect(assigned).toBeDisabled()
    expect(assigned).toHaveTextContent(i18n.t('dealOwner.takeOverDisabled.assigned', { owner: 'Kim Koller' }))
    await user.keyboard('{Escape}')

    await user.click(second)
    const closed = await screen.findByRole('menuitem', { name: /Übernehmen/ })
    expect(closed).toBeDisabled()
    expect(closed).toHaveTextContent(i18n.t('dealOwner.takeOverDisabled.closed'))
  })
})

describe('Contract detail — the seller picker and «Übernehmen» (KAN-405)', () => {
  function renderContract(routes: FakeRoute[]) {
    const backend = installFakeBackend([
      ...routes,
      SELLERS,
      { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/sales\/contracts\/k1\/line-items$/, handler: () => ({ items: [] }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
      </Routes>,
      { route: '/sales/contracts/k1' },
    )
    return backend
  }

  it('sends the chosen seller as ownerUserId with If-Match', async () => {
    const user = userEvent.setup()
    const backend = renderContract([
      { method: 'PATCH', match: /^\/sales\/contracts\/k1$/, handler: () => ({ ...CONTRACT, ownerUserId: 'u-2', ownerLabel: 'Kim Koller', version: 4 }) },
    ])

    await user.click(await screen.findByRole('textbox', { name: 'Verantwortlicher Verkäufer' }))
    await user.click(await screen.findByRole('option', { name: 'Kim Koller' }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')
    expect(patch.body).toEqual({ ownerUserId: 'u-2' })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('takes an unassigned contract over from the overflow', async () => {
    const user = userEvent.setup()
    const backend = renderContract([
      { method: 'PATCH', match: /^\/sales\/contracts\/k1$/, handler: () => ({ ...CONTRACT, ownerUserId: 'user-1', ownerLabel: 'Test Advisor', version: 4 }) },
    ])

    await user.click(await screen.findByRole('button', { name: i18n.t('common.moreActions') }))
    await user.click(await screen.findByRole('menuitem', { name: /Übernehmen/ }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')[0].body).toEqual({ ownerUserId: 'user-1' })
  })
})

describe('A sent offer — the seller alone may change (KAN-405, option A)', () => {
  it('shows the current seller and clears it to «Nicht zugewiesen»', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      SELLERS,
      { method: 'GET', match: /^\/sales\/offers\/o-9$/, handler: () => OPEN_OFFER },
      { method: 'PATCH', match: /^\/sales\/offers\/o-9$/, handler: () => ({ ...OPEN_OFFER, ownerUserId: null, ownerLabel: null, version: 5 }) },
      { match: /^\/sales\/offers\/o-9\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferDetailPage />} />
      </Routes>,
      { route: '/sales/offers/o-9' },
    )

    const picker = await screen.findByRole('textbox', { name: 'Verantwortlicher Verkäufer' })
    await waitFor(() => expect(picker).toHaveValue('Kim Koller'))
    const clear = picker.closest('.mantine-Select-root')?.querySelector('button') as HTMLButtonElement
    await user.click(clear)

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')
    expect(patch.body).toEqual({ ownerUserId: null })
    expect(patch.headers.get('If-Match')).toBe('4')
  })
})

describe('A sent offer — «Übernehmen» in the overflow (KAN-405)', () => {
  it('takes the unassigned offer with the version the screen holds', async () => {
    const user = userEvent.setup()
    const unassigned = { ...OPEN_OFFER, ownerUserId: null, ownerLabel: null }
    const backend = installFakeBackend([
      SELLERS,
      { method: 'GET', match: /^\/sales\/offers\/o-9$/, handler: () => unassigned },
      { method: 'PATCH', match: /^\/sales\/offers\/o-9$/, handler: () => ({ ...unassigned, ownerUserId: 'user-1', ownerLabel: 'Test Advisor', version: 5 }) },
      { match: /^\/sales\/offers\/o-9\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferDetailPage />} />
      </Routes>,
      { route: '/sales/offers/o-9' },
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('common.moreActions') }))
    await user.click(await screen.findByRole('menuitem', { name: /Übernehmen/ }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')
    expect(patch.body).toEqual({ ownerUserId: 'user-1' })
    expect(patch.headers.get('If-Match')).toBe('4')
  })
})

describe('The draft offer workspace — the seller picker (KAN-405)', () => {
  it('sends the chosen seller through the save queue, in French labels too', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const draft = { ...OPEN_OFFER, status: 'draft', containers: [], ownerUserId: null, ownerLabel: null, customerId: null }
    const backend = installFakeBackend([
      SELLERS,
      { method: 'GET', match: /^\/sales\/offers\/o-9$/, handler: () => draft },
      { method: 'PATCH', match: /^\/sales\/offers\/o-9$/, handler: () => ({ ...draft, ownerUserId: 'u-2', ownerLabel: 'Kim Koller', version: 5 }) },
      { match: /^\/sales\/offers\/o-9\/line-items$/, handler: () => ({ items: [] }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o-9' },
    )

    const picker = await screen.findByRole('textbox', { name: 'Vendeur responsable' })
    expect(picker).toHaveAttribute('placeholder', 'Non attribué')
    await user.click(picker)
    await user.click(await screen.findByRole('option', { name: 'Kim Koller' }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/offers\/o-9$/, 'PATCH')
    expect(patch.body).toEqual({ ownerUserId: 'u-2' })
    expect(patch.headers.get('If-Match')).toBe('4')
    expect(within(document.body).queryByText(/MISSING I18N KEY/)).not.toBeInTheDocument()
  })
})
