// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import type { SalesOfferRead } from '../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-442 — useDebouncedNumberField re-synced from the server only when
// the offer's id changed. A refetch carrying a colleague's save was never
// shown, but the save queue (KAN-314) took If-Match from the refreshed
// cache: the next edit of the stale figure was sent with the new version
// and landed, overwriting the colleague's price without a 409.

const offerAt = (version: number, overrides: Partial<SalesOfferRead> = {}): SalesOfferRead => ({
  id: 'o1',
  offerNumber: 'ANG-2026-0327',
  status: 'draft',
  customerId: 'cust-1',
  customerLabel: 'Hans Muster',
  customerLocality: null,
  vehicleSource: 'stock',
  stockItemId: 'st-1',
  vehicleLabel: 'VW Golf 2024',
  manualVehicleCondition: null,
  manualBasePrice: null,
  leasingDownPayment: null,
  leasingTermMonths: null,
  leasingKmPerYear: null,
  basePrice: '30000.00',
  optionsTotal: null,
  listPrice: '30000.00',
  accessoriesTotal: null,
  totalBeforeDiscount: '30000.00',
  discountType: 'amount',
  discountValue: null,
  discountAmount: null,
  grossPrice: '30000.00',
  costBasis: null,
  margin: null,
  vehicleSnapshotFrozenAt: '2026-09-17T00:00:00Z',
  tradeInVehicleId: null,
  tradeInLabel: null,
  tradeInVin: null,
  tradeInValuationId: null,
  tradeInValue: null,
  tradeInPurchasePrice: null,
  payable: null,
  cancelledReason: null,
  copiedFromOfferId: null,
  containers: [
    { id: 'customer', requirement: 'required', status: 'complete' },
    { id: 'vehicle', requirement: 'required', status: 'complete' },
    { id: 'pricing', requirement: 'required', status: 'complete' },
    { id: 'trade_in', requirement: 'optional', status: 'not_started' },
    { id: 'leasing', requirement: 'optional', status: 'not_started' },
  ],
  vehicleCondition: 'new',
  version,
  createdAt: '2026-02-01T00:00:00Z',
  updatedAt: '2026-02-01T00:00:00Z',
  ...overrides,
})

function setUp(initial: SalesOfferRead) {
  let current = initial
  let hold: Promise<void> | null = null
  const backend = installFakeBackend([
    {
      method: 'PATCH',
      match: /^\/sales\/offers\/o1$/,
      handler: async (req) => {
        if (hold) await hold
        if (req.headers.get('If-Match') !== String(current.version)) {
          return status(409, {
            error: { code: 'conflict', message: 'stale', details: { currentVersion: current.version, ifMatchVersion: Number(req.headers.get('If-Match')) } },
          })
        }
        current = { ...current, ...(req.body as object), version: current.version + 1 }
        return current
      },
    },
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => current },
    { match: /^\/integrations\/capabilities\/packages$/, handler: () => ({ capabilityCode: 'packages', granted: true }) },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/cust-1$/, handler: () => customer({ id: 'cust-1', firstName: 'Hans', lastName: 'Muster' }) },
  ])
  const view = renderWithProviders(
    <Routes>
      <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
    </Routes>,
    { route: '/sales/offers/o1' },
  )
  /** A colleague's save lands at the server, and this screen refetches. */
  const colleagueSaves = async (patch: Partial<SalesOfferRead>) => {
    current = { ...current, ...patch, version: current.version + 1 }
    await act(() => view.queryClient.invalidateQueries({ queryKey: ['sales-offer', 'o1'] }))
  }
  /** A colleague's save lands while one of ours is held at the server: the
   * fake backend answers one request at a time, so their copy reaches this
   * screen's cache as a refetch's answer would, without a request. */
  const colleagueSavesWhileOursIsOut = async (patch: Partial<SalesOfferRead>) => {
    current = { ...current, ...patch, version: current.version + 1 }
    await act(async () => {
      view.queryClient.setQueryData(['sales-offer', 'o1'], current)
    })
  }
  /** Holds every PATCH at the server until the returned release is called. */
  const holdSaves = () => {
    let release!: () => void
    hold = new Promise<void>((resolve) => {
      release = () => {
        hold = null
        resolve()
      }
    })
    return release
  }
  return { backend, colleagueSaves, colleagueSavesWhileOursIsOut, holdSaves }
}

const discountField = () => screen.findByRole('textbox', { name: i18n.t('offerWorkspace.pricing.discountValue') })
const patches = (backend: ReturnType<typeof setUp>['backend']) => backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')

describe('a debounced number field shows a newer server copy (KAN-442)', () => {
  it('shows the colleague\'s discount after a refetch, and the next edit is sent with the new If-Match', async () => {
    const { backend, colleagueSaves } = setUp(offerAt(1, { discountValue: '500.00' }))
    const field = await discountField()
    await waitFor(() => expect(field).toHaveValue("CHF 500.00"))

    await colleagueSaves({ discountValue: '800.00' })
    await waitFor(() => expect(field).toHaveValue("CHF 800.00"))

    fireEvent.change(field, { target: { value: '900' } })
    await waitFor(() => expect(patches(backend)).toHaveLength(1), { timeout: 2000 })
    const [patch] = patches(backend)
    expect(patch.body).toMatchObject({ discountValue: 900 })
    expect(patch.headers.get('If-Match')).toBe('2')
  })

  it('never replaces a figure still being typed', async () => {
    const { backend, colleagueSaves } = setUp(offerAt(1, { discountValue: '500.00' }))
    const field = await discountField()
    await waitFor(() => expect(field).toHaveValue("CHF 500.00"))

    fireEvent.change(field, { target: { value: '950' } })
    await colleagueSaves({ discountValue: '800.00' })
    expect(field).toHaveValue("CHF 950")

    await waitFor(() => expect(patches(backend)).toHaveLength(1), { timeout: 2000 })
    expect(patches(backend)[0].body).toMatchObject({ discountValue: 950 })
    expect(field).not.toHaveValue("CHF 800.00")
  })

  it('a colleague\'s figure that arrived while this field\'s save was out is shown once that save is refused', async () => {
    const { backend, colleagueSavesWhileOursIsOut, holdSaves } = setUp(offerAt(1, { discountValue: '500.00' }))
    const field = await discountField()
    await waitFor(() => expect(field).toHaveValue("CHF 500.00"))

    const release = holdSaves()
    fireEvent.change(field, { target: { value: '900' } })
    await waitFor(() => expect(patches(backend)).toHaveLength(1), { timeout: 2000 })
    // While our save is out, the colleague's 800 reaches this screen's cache:
    // the field must not take it yet (its own save is pending).
    await colleagueSavesWhileOursIsOut({ discountValue: '800.00' })
    await new Promise((resolve) => setTimeout(resolve, 50)) // the cache notifies its observers on a timer
    expect((field as HTMLInputElement).value).toMatch(/^CHF 900(\.00)?$/)

    // Our save (If-Match 1) is refused; the reload brings the same 800, so
    // only the refusal itself can make the field show it.
    await act(async () => release())
    await waitFor(() => expect((field as HTMLInputElement).value).toMatch(/^CHF 800(\.00)?$/), { timeout: 2000 })

    fireEvent.change(field, { target: { value: '950' } })
    await waitFor(() => expect(patches(backend)).toHaveLength(2), { timeout: 2000 })
    expect(patches(backend)[1].headers.get('If-Match')).toBe('2')
    expect(patches(backend)[1].body).toMatchObject({ discountValue: 950 })
  })

  // Review of 7decc88: the old hook committed only when its debounced copy
  // changed, so typing back the figure shown before an adoption was never saved.
  it('typing back the figure shown before a colleague\'s change is saved', async () => {
    const { backend, colleagueSaves } = setUp(offerAt(1, { discountValue: '500.00' }))
    const field = await discountField()
    await waitFor(() => expect(field).toHaveValue("CHF 500.00"))

    await colleagueSaves({ discountValue: '800.00' })
    await waitFor(() => expect(field).toHaveValue("CHF 800.00"))
    fireEvent.change(field, { target: { value: '500' } })

    await waitFor(() => expect(patches(backend)).toHaveLength(1), { timeout: 2000 })
    expect(patches(backend)[0].body).toMatchObject({ discountValue: 500 })
    expect(patches(backend)[0].headers.get('If-Match')).toBe('2')
  })
})
