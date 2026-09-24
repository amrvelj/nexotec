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
