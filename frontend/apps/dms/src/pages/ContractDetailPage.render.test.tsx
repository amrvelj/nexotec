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

const CONTRACT = {
  id: 'k1',
  contractNumber: 'C-000001',
  offerNumber: 'O-000001',
  status: 'pending',
  version: 3,
  vehicleLabel: 'Seat Leon',
  grossPrice: null,
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
