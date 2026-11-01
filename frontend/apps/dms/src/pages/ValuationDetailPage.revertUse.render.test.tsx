// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-253 (Anto, 2026-10-31, option C) — «Verwendung aufheben»: the dealer
// manager clears a «Verwendet» stamp no signed contract stands behind, with a
// stated reason. Anyone the session does not let write
// `valuation_use_reversal` is not offered the entry at all (KAN-430).

const USED = {
  id: 'val-1', valuationNumber: 'B-000007', status: 'used', source: 'manual', hasSignedContract: false,
  customerId: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null, vehicleVin: null,
  usedAt: '2026-09-01T08:00:00Z', finalOffer: '12000.00', deductions: [], version: 4, validFrom: '2026-10-01', validUntil: '2026-12-31',
  vehicleFirstRegistration: null, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

function session(isDealerManager: boolean, writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Mia', lastName: 'Muster', email: 'mia@example.ch', accessRoles: ['sales'], isDealerManager },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function install(sessionRoute: FakeRoute, { refuse = false } = {}) {
  return installFakeBackend([
    sessionRoute,
    { method: 'GET', match: /^\/valuations\/val-1$/, handler: () => USED },
    {
      method: 'POST',
      match: /^\/valuations\/val-1\/revert-use$/,
      handler: () =>
        refuse
          ? status(409, {
              error: { code: 'conflict', message: 'A signed contract carries it.', details: { reason: 'signed_contract_carries_it' } },
            })
          : { ...USED, status: 'valid', version: 5 },
    },
  ])
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/valuations/:id" element={<ValuationDetailPage />} />
    </Routes>,
    { route: '/valuations/val-1' },
  )
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('B-000007')
  await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
}

afterEach(() => cleanup())

describe('ValuationDetailPage — «Verwendung aufheben» (KAN-253)', () => {
  it('is not offered to a salesperson whose session may not write it', async () => {
    const user = userEvent.setup()
    install(session(false, ['valuations']))
    renderPage()

    await openMenu(user)

    await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.markUsed')) })
    expect(screen.queryByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.revertUse')) })).toBeNull()
  })

  it('the manager states a reason and clears the stamp; a blank reason is refused before the round trip', async () => {
    const user = userEvent.setup()
    const backend = install(session(true, ['valuations', 'valuation_use_reversal']))
    renderPage()

    await openMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.revertUse')) }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain(i18n.t('valuationDetail.revertUseDialog.body', { number: 'B-000007' }))

    await user.click(screen.getByRole('button', { name: i18n.t('valuationDetail.revertUseDialog.submit') }))
    expect(await screen.findByText(i18n.t('valuationDetail.revertUseDialog.reasonRequired'))).toBeTruthy()
    expect(backend.callsTo(/\/revert-use$/, 'POST')).toHaveLength(0)

    await user.type(screen.getByRole('textbox', { name: new RegExp(i18n.t('valuationDetail.revertUseDialog.reasonLabel')) }), ' Alter Handstempel ')
    await user.click(screen.getByRole('button', { name: i18n.t('valuationDetail.revertUseDialog.submit') }))

    await waitFor(() => expect(backend.callsTo(/\/revert-use$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/revert-use$/, 'POST')
    expect(call.headers.get('If-Match')).toBe('4')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(call.body).toEqual({ reason: 'Alter Handstempel' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('a refusal is said by its reason in the UI language', async () => {
    const user = userEvent.setup()
    install(session(true, ['valuations', 'valuation_use_reversal']), { refuse: true })
    renderPage()

    await openMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.revertUse')) }))
    await user.type(await screen.findByRole('textbox', { name: new RegExp(i18n.t('valuationDetail.revertUseDialog.reasonLabel')) }), 'Versuch')
    await user.click(screen.getByRole('button', { name: i18n.t('valuationDetail.revertUseDialog.submit') }))

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('apiRefusal.reasons.signed_contract_carries_it'))
  })
})
