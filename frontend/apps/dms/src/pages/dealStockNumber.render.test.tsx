// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { SalesContractRead, SalesOfferRead } from '../api/types'
import { DealVehicleContainer } from '../components/deal-workspace/DealWorkspaceContainers'
import { ContractDetailPage } from './ContractDetailPage'
import { OfferDetailContent } from './OfferDetailPage'
import { SalesListPage } from './SalesListPage'

// KAN-570 (Anto, 2026-11-05, option A) — the stock number an offer or a
// contract carries since KAN-157 is shown beside the car: in the vehicle
// container of the offer workspace and the pending contract, in the header
// of the sent offer and of a contract past editing. It opens the stock
// detail as an overlay (ADR-059). The Sales grid offers it as a column,
// hidden by default.

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

const STOCK = 'S-000042'
const stockNumberText = () => i18n.t('offerWorkspace.vehicle.stockNumber', { stockNumber: STOCK })

const OFFER = {
  id: 'o1',
  offerNumber: 'O-000001',
  status: 'open',
  version: 2,
  vehicleSource: 'stock',
  stockItemId: 's1',
  stockItemLabel: STOCK,
  vehicleLabel: 'Seat Leon',
} as unknown as SalesOfferRead

const CONTRACT = {
  id: 'k1',
  contractNumber: 'C-000001',
  offerNumber: 'O-000001',
  status: 'confirmed',
  version: 3,
  customerId: 'cust-1',
  customerLabel: 'Ursula Vogt',
  vehicleSource: 'stock',
  stockItemId: 's1',
  stockItemLabel: STOCK,
  vehicleLabel: 'Seat Leon',
  grossPrice: '32000.00',
  tradeInValue: null,
  payable: null,
  margin: null,
  financing: null,
} as unknown as SalesContractRead

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

async function opensTheStockDetail(backend: ReturnType<typeof installFakeBackend>) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: stockNumberText() }))
  await waitFor(() => expect(backend.callsTo(/^\/inventory\/stock-items\/s1$/, 'GET').length).toBeGreaterThan(0))
}

describe('the vehicle container shows the stock number beside the car (KAN-570)', () => {
  for (const ownerType of ['offer', 'contract'] as const) {
    it(`${ownerType === 'offer' ? 'offer workspace' : 'pending contract'}: «Lagernummer» beside the car, opening the stock detail`, async () => {
      const onOpenStockItem = vi.fn()
      installFakeBackend([])
      const deal = (ownerType === 'offer' ? OFFER : { ...CONTRACT, status: 'pending' }) as SalesOfferRead
      renderWithProviders(
        <DealVehicleContainer ownerType={ownerType} deal={deal} patch={async () => {}} save={vi.fn()} onOpenStockItem={onOpenStockItem} />,
      )

      expect(screen.getByText('Seat Leon')).toBeInTheDocument()
      expect(screen.getByText('Lagernummer S-000042')).toBeInTheDocument()
      await userEvent.setup().click(screen.getByRole('button', { name: stockNumberText() }))
      expect(onOpenStockItem).toHaveBeenCalledTimes(1)
    })
  }

  it('a manual car shows no stock number', () => {
    installFakeBackend([])
    const manual = { ...OFFER, vehicleSource: 'manual', stockItemId: null, stockItemLabel: null } as unknown as SalesOfferRead
    renderWithProviders(<DealVehicleContainer ownerType="offer" deal={manual} patch={async () => {}} save={vi.fn()} />)

    expect(screen.getByText('Seat Leon')).toBeInTheDocument()
    expect(screen.queryByTestId('deal-stock-number')).not.toBeInTheDocument()
  })
})

describe('the sent offer and the signed contract show it in their header (KAN-570)', () => {
  it('sent offer: beside the car, the stock detail opens as an overlay', async () => {
    const backend = installFakeBackend([
      { match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
      { match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<OfferDetailContent offerId="o1" embedded />)

    expect(await screen.findByText('Lagernummer S-000042')).toBeInTheDocument()
    await opensTheStockDetail(backend)
  })

  it('signed contract: beside the car, the stock detail opens as an overlay', async () => {
    const backend = installFakeBackend([
      { match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
      </Routes>,
      { route: '/sales/contracts/k1' },
    )

    expect(await screen.findByText('Lagernummer S-000042')).toBeInTheDocument()
    await opensTheStockDetail(backend)
  })

  it('in French too', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
      { match: /^\/sales\/offers\/o1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<OfferDetailContent offerId="o1" embedded />)

    expect(await screen.findByText('N° de stock S-000042')).toBeInTheDocument()
  })
})

describe('the Sales grid offers the stock number as a column, hidden by default (KAN-570)', () => {
  it('not shown until picked in the column panel, then shown per row', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        match: /^\/sales\/deals$/,
        handler: () => ({
          items: [
            {
              id: 'd-1', entityType: 'contract', number: 'V-2026-0001', status: 'pending', offerId: null, offerNumber: null,
              contractId: 'c-1', contractNumber: 'V-2026-0001', customerId: null, customerLabel: 'Ursula Vogt',
              customerLocality: null, vehicleLabel: 'VW Golf', stockItemLabel: STOCK, ownerUserId: null, ownerLabel: null,
              grossPrice: '25000.00', margin: null, documentsCount: 0, createdAt: '2026-10-01T10:00:00Z',
              updatedAt: '2026-10-02T10:00:00Z',
            },
          ],
          nextCursor: null,
          total: 1,
          totalIsEstimate: false,
        }),
      },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })
    await screen.findByText('VW Golf')
    const header = i18n.t('salesList.columns.stockNumber')
    expect(screen.queryByRole('columnheader', { name: new RegExp(header) })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
    await user.click(screen.getByRole('checkbox', { name: new RegExp(`^${header}$`) }))

    expect(await screen.findByRole('columnheader', { name: new RegExp(header) })).toBeInTheDocument()
    expect(screen.getByText(STOCK)).toBeInTheDocument()
  })
})
