// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend, status } from '../../../test/fakeBackend'
import { contact } from '../../../test/fixtures'
import type { SalesOfferRead } from '../../../api/types'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-656 — a contact merged into another is refused as an offer's buyer
// (409 `contact_merged`, naming the survivor). The workspace's banner says
// why in the UI language, and the contact container offers the survivor.

const offer = (overrides: Partial<SalesOfferRead> = {}) =>
  ({
    id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', contactId: null, contactLabel: null,
    vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
    tradeInLabel: null, tradeInVin: null, tradeInValuationId: null, tradeInValue: null, containers: [], version: 4,
    leasingDownPayment: null, leasingTermMonths: null, leasingKmPerYear: null,
    liveContractId: null, liveContractNumber: null,
    ...overrides,
  }) as unknown as SalesOfferRead

function install() {
  let current = offer()
  const backend = installFakeBackend([
    {
      method: 'GET',
      match: /^\/contacts$/,
      handler: () => ({
        items: [contact({ id: 'cust-dup', contactNumber: 'K-000008', firstName: 'Ursi', lastName: 'Vogt' })],
        nextCursor: null,
      }),
    },
    {
      method: 'GET',
      match: /^\/contacts\/cust-9$/,
      handler: () => contact({ id: 'cust-9', contactNumber: 'K-000009', firstName: 'Ursula', lastName: 'Vogt' }),
    },
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => current },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    {
      method: 'PATCH',
      match: /^\/sales\/offers\/o1$/,
      handler: (req) => {
        if ((req.body as { contactId?: string }).contactId === 'cust-dup') {
          return status(409, {
            error: {
              code: 'conflict',
              message: 'Contact K-000008 has been merged into another — cannot be attached to an offer.',
              details: { reason: 'contact_merged', mergedIntoContactId: 'cust-9' },
            },
          })
        }
        current = { ...current, contactId: 'cust-9', contactLabel: 'Ursula Vogt', version: current.version + 1 }
        return current
      },
    },
  ])
  return backend
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('OfferWorkspace — a merged contact is refused as the buyer (KAN-656)', () => {
  it('says so in French and attaches the survivor in one click', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(
      <Routes>
        <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
      </Routes>,
      { route: '/sales/offers/o1' },
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.contact.search') }))
    await user.type(await screen.findByPlaceholderText(i18n.t('offerWorkspace.contact.search')), 'Vogt')
    await user.click(await screen.findByText('Ursi Vogt'))

    // Hardcoded on purpose: a key missing from the French bundle must not pass.
    expect(await screen.findByText('Ce contact a été fusionné avec un autre et ne peut plus être choisi comme acheteur.')).toBeInTheDocument()
    expect(await screen.findByText('Le contact choisi a été fusionné avec Ursula Vogt (K-000009).')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Choisir Ursula Vogt' }))

    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH')).toHaveLength(2))
    expect(backend.callsTo(/^\/sales\/offers\/o1$/, 'PATCH').map((c) => c.body)).toEqual([
      { contactId: 'cust-dup' },
      { contactId: 'cust-9' },
    ])
    expect(await screen.findByText('Ursula Vogt')).toBeInTheDocument()
    expect(screen.queryByText('Le contact choisi a été fusionné avec Ursula Vogt (K-000009).')).not.toBeInTheDocument()
  })
})
