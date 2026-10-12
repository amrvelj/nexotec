// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesContractRead } from '../api/types'
import { ContractDetailPage } from './ContractDetailPage'

// KAN-55 (D-20): confirming a contract is refused when the customer has no
// usable address — through the same 409 path as the credit block. The
// refusal used to be an unhandled promise rejection with zero feedback;
// it now renders as a localised inline alert keyed off `details.reason`.

// vehicleSource/grossPrice non-null (KAN-66 / G-67) — this fixture stands
// for an ordinary offer-backed contract, which the client's own
// vehicle/price completeness gate must never disable; the "empty contract"
// describe block below overrides both to null on purpose.
const CONTRACT = {
  id: 'k1',
  contractNumber: 'C-000001',
  offerNumber: 'O-000001',
  status: 'pending',
  version: 3,
  customerId: 'cust-1',
  customerLabel: 'Ursula Vogt',
  vehicleSource: 'stock',
  vehicleLabel: 'Seat Leon',
  grossPrice: '32000.00',
  tradeInValue: null,
  payable: null,
  margin: null,
  financing: null,
} as unknown as SalesContractRead

function refuseConfirm(reason: string, extra: Record<string, unknown> = {}) {
  return installFakeBackend([
    { match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
    { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /^\/sales\/contracts\/k1\/confirm$/,
      handler: () =>
        status(409, { error: { code: 'conflict', message: 'Customer has no usable address — contract C-000001 …', details: { reason, ...extra } } }),
    },
  ])
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
    </Routes>,
    { route: '/sales/contracts/k1' },
  )
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ContractDetailPage — the confirm refusal is surfaced and localised (KAN-55)', () => {
  it('a missing-address 409 renders the localised inline alert, not an unhandled rejection', async () => {
    const user = userEvent.setup()
    refuseConfirm('missing_address')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.missingAddress'))).toBeInTheDocument()
  })

  it('the same alert path carries the credit-block reason (one mechanism, two reasons)', async () => {
    const user = userEvent.setup()
    refuseConfirm('credit_block', { creditBlockReason: 'Zahlungsverzug' })
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(
      await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.creditBlock', { reason: 'Zahlungsverzug' })),
    ).toBeInTheDocument()
  })

  it('renders the refusal in French', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    refuseConfirm('missing_address')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    const fr = i18n.t('contractDetail.errors.confirmRefused.missingAddress')
    expect(fr).not.toBe('No address on file — needed before a contract can be confirmed.')
    expect(await screen.findByText(fr)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(fr).textContent).not.toMatch(/⚠ MISSING I18N KEY/))
  })

  it('a missing-vehicle 409 (KAN-66 / G-67) renders the localised inline alert', async () => {
    const user = userEvent.setup()
    refuseConfirm('missing_vehicle')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.missingVehicle'))).toBeInTheDocument()
  })

  it('a missing-price 409 renders the localised inline alert', async () => {
    const user = userEvent.setup()
    refuseConfirm('missing_price')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.missingPrice'))).toBeInTheDocument()
  })

  it('an expired trade-in valuation 409 (KAN-101) renders the localised inline alert', async () => {
    const user = userEvent.setup()
    refuseConfirm('trade_in_valuation_expired')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(
      await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.tradeInValuationExpired')),
    ).toBeInTheDocument()
  })

  it('a sold car (KAN-147, 409 vehicle_left_stock) renders the localised inline alert', async () => {
    const user = userEvent.setup()
    refuseConfirm('vehicle_left_stock', { stockItemId: 'si-1' })
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.vehicleLeftStock'))).toBeInTheDocument()
  })

  it('a stale-version 409 (no reason) shows the localised reload message, never the backend English string', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      { match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/sales\/contracts\/k1\/confirm$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'SalesContract has been modified since If-Match version 3 (current version is 4).',
              details: { currentVersion: 4, ifMatchVersion: 3 },
            },
          }),
      },
    ])
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.staleVersion'))).toBeInTheDocument()
    expect(screen.queryByText(/SalesContract has been modified/)).not.toBeInTheDocument()
  })
})

// KAN-66 (G-67) — a contract born from a customer with no offer (KAN-58)
// has no vehicle/price at all. ADR-061: the primary action must not invite
// a click that will only be refused — it disables itself, with a reason,
// before the user ever gets to the 409.
describe('ContractDetailPage — an empty contract (KAN-66 / G-67) disables confirm before the click', () => {
  it('a pending contract with no vehicle and no price shows the confirm action disabled', async () => {
    installFakeBackend([
      {
        match: /^\/sales\/contracts\/k1$/,
        handler: () => ({ ...CONTRACT, vehicleSource: null, grossPrice: null }),
      },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderPage()

    // The reason itself lives in a Mantine Tooltip anchored to the disabled
    // button (DetailHeader's own HeaderActionButton), revealed on hover —
    // not asserted here, matching how this codebase's other
    // disabled-primary-action test (OfferWorkspace.pricingRequired) sticks
    // to the disabled state itself rather than the tooltip's mount timing.
    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()
  })

  it('a pending contract with a vehicle and a price leaves confirm enabled', async () => {
    installFakeBackend([
      {
        match: /^\/sales\/contracts\/k1$/,
        handler: () => ({ ...CONTRACT, vehicleSource: 'stock', grossPrice: '32000.00' }),
      },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderPage()

    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
  })
})

// KAN-147 — a sale migrated from the legacy system was invoiced there; the
// backend refuses its cancellation (409 legacy_sale), so the action is
// disabled before the click (ADR-061), with its own reason.
describe('ContractDetailPage — a legacy sale cannot be cancelled (KAN-147)', () => {
  it('a confirmed legacy contract shows cancel disabled', async () => {
    installFakeBackend([
      { match: /^\/sales\/contracts\/k1$/, handler: () => ({ ...CONTRACT, status: 'confirmed', isLegacy: true }) },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('common.moreActions') }))
    const item = await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contractDetail.actions.cancel')) })
    expect(item).toBeDisabled()
    expect(item).toHaveTextContent(i18n.t('contractDetail.actions.cancelDisabledReasonLegacy'))
  })

  it('a confirmed contract created live keeps cancel enabled', async () => {
    installFakeBackend([
      { match: /^\/sales\/contracts\/k1$/, handler: () => ({ ...CONTRACT, status: 'confirmed', isLegacy: false }) },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('common.moreActions') }))
    expect(
      await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contractDetail.actions.cancel')) }),
    ).toBeEnabled()
  })
})

describe('ContractDetailPage — the trade-in figure carries its source (KAN-101)', () => {
  it('marks a manual trade-in valuation as manual next to the trade-in value', async () => {
    const withTradeIn = { ...CONTRACT, tradeInValue: '12000.00', tradeInValuationId: 'val-42' } as SalesContractRead
    installFakeBackend([
      { match: /^\/sales\/contracts\/k1$/, handler: () => withTradeIn },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/valuations\/val-42$/, handler: () => ({ id: 'val-42', source: 'manual', status: 'used' }) },
    ])
    renderPage()

    expect(await screen.findByText(i18n.t('valuationSource.manual'))).toBeInTheDocument()
  })
})

// KAN-307 (Anto, 2026-10-12, amending ADR-083) — a contract started from a
// stock car has no buyer yet. The pending contract picks one in the offer
// workspace's own customer container, and is never confirmed without one:
// the action is disabled with that reason, and the server's
// `missing_customer` refusal is localised like the others.
describe('ContractDetailPage — the buyer is picked in the contract (KAN-307)', () => {
  const NO_BUYER = {
    ...CONTRACT, offerNumber: null, customerId: null, customerLabel: null, vehicleSnapshotFrozenAt: '2026-10-12T08:00:00Z',
    containers: [{ id: 'customer', requirement: 'required', status: 'not_started' }],
  }

  it('a pending contract without a buyer offers the customer search and keeps confirm disabled', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => NO_BUYER },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderPage()

    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeDisabled()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.customer.search') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('offerWorkspace.customer.createNew') })).toBeInTheDocument()
    // The contract's own hint, never the offer's ("eine Offerte trägt den Namen …").
    expect(screen.getByText(i18n.t('offerWorkspace.customer.emptyHintContract'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('offerWorkspace.customer.emptyHint'))).toBeNull()
  })

  it('picking a customer sets the contract\'s buyer with its If-Match', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => NO_BUYER },
      { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'GET',
        match: /^\/customers$/,
        handler: () => ({ items: [{ id: 'cust-9', customerNumber: 'K-000009', firstName: 'Ursula', lastName: 'Vogt', companyName: null }], nextCursor: null }),
      },
      {
        method: 'PATCH',
        match: /^\/sales\/contracts\/k1$/,
        handler: () => ({ ...CONTRACT, customerId: 'cust-9', customerLabel: 'Ursula Vogt', version: 4 }),
      },
    ])
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.customer.search') }))
    await user.type(await screen.findByPlaceholderText(i18n.t('offerWorkspace.customer.search')), 'Vogt')
    await user.click(await screen.findByText('Ursula Vogt'))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/^\/sales\/contracts\/k1$/, 'PATCH')
    expect(patch.body).toEqual({ customerId: 'cust-9' })
    expect(patch.headers.get('If-Match')).toBe('3')
    expect(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') })).toBeEnabled()
  })

  it('a missing-customer 409 renders the localised inline alert', async () => {
    const user = userEvent.setup()
    refuseConfirm('missing_customer')
    renderPage()

    await user.click(await screen.findByRole('button', { name: i18n.t('contractDetail.actions.confirm') }))

    expect(await screen.findByText(i18n.t('contractDetail.errors.confirmRefused.missingCustomer'))).toBeInTheDocument()
  })
})
