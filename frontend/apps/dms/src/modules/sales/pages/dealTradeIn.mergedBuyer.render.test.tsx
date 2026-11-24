// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../../test/fakeBackend'
import { contact } from '../../../test/fixtures'
import type { SalesContractRead, SalesOfferRead } from '../../../api/types'
import { ContractDetailPage } from './ContractDetailPage'
import { OfferWorkspacePage } from './OfferWorkspacePage'

// KAN-382 — a trade-in on a deal that still pointed at a merged contact was
// refused 409 `contact_merged` (KAN-365). The offer workspace and the
// pending contract said only "could not be saved"; they say that the
// contact was merged, naming the survivor when the API names one. Since
// KAN-189 this server no longer sends it (the trade-in allocates no party);
// the screen still answers it, for an older instance during a rolling deploy.

const API_MESSAGE = 'This contact has been merged into another; give the vehicle role to the contact it was merged into.'

const merged = (mergedIntoContactId: string | null) => () =>
  status(409, { error: { code: 'conflict', message: API_MESSAGE, details: { reason: 'contact_merged', mergedIntoContactId } } })

const SURVIVOR = contact({ id: 'cust-2', contactNumber: 'K-000002', firstName: 'Anna', lastName: 'Muster' })
const survivorRoute: FakeRoute = { method: 'GET', match: /^\/contacts\/cust-2$/, handler: () => SURVIVOR }

const OFFER = {
  id: 'o1', offerNumber: 'ANG-2026-0001', status: 'draft', contactId: null, contactLabel: null,
  vehicleSource: null, stockItemId: null, vehicleLabel: null, tradeInVehicleId: null, tradeInConfigurationId: null,
  tradeInLabel: null, tradeInVin: null, tradeInValuationId: null, tradeInValue: null, containers: [], version: 4,
} as unknown as SalesOfferRead

const CONTRACT = {
  id: 'k1', contractNumber: 'C-000007', offerId: null, offerNumber: null, status: 'pending', contactId: null,
  contactLabel: null, contactLocality: null, vehicleSource: null, stockItemId: null, vehicleLabel: null,
  configurationId: null, configurationLabel: null, tradeInVehicleId: null, tradeInLabel: null, tradeInVin: null,
  tradeInConfigurationId: null, tradeInValuationId: null, tradeInValue: null, financing: 'cash', reservationId: null,
  signedAt: null, isInvoiceable: false, isLegacy: false, containers: [], version: 1,
} as unknown as SalesContractRead

function installOffer(answer: () => unknown, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/sales\/offers\/o1$/, handler: () => OFFER },
    { method: 'GET', match: /^\/sales\/offers\/o1\/line-items$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    { method: 'POST', match: /^\/sales\/offers\/o1\/trade-in$/, handler: answer },
  ])
}

function installContract(answer: () => unknown, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/sales\/contracts\/k1$/, handler: () => CONTRACT },
    { match: /^\/sales\/contracts\/k1\/documents$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/contracts\/k1\/line-items$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /^\/integrations\/capabilities\//, handler: () => ({ capabilityCode: 'x', granted: false }) },
    { method: 'POST', match: /^\/sales\/contracts\/k1\/trade-in$/, handler: answer },
  ])
}

const SCREENS = {
  'the offer workspace': {
    install: installOffer,
    render: () =>
      renderWithProviders(
        <Routes>
          <Route path="/sales/offers/:id" element={<OfferWorkspacePage />} />
        </Routes>,
        { route: '/sales/offers/o1' },
      ),
  },
  'contract edit': {
    install: installContract,
    render: () =>
      renderWithProviders(
        <Routes>
          <Route path="/sales/contracts/:id" element={<ContractDetailPage />} />
        </Routes>,
        { route: '/sales/contracts/k1' },
      ),
  },
}

async function saveTradeIn() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.tradeIn.add') }))
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.vinLabel')), 'WVWZZZ1KZAW331338')
  await user.type(screen.getByLabelText(i18n.t('offerWorkspace.tradeIn.labelLabel')), 'VW Golf')
  await user.click(screen.getByRole('button', { name: i18n.t('common.save') }))
}

beforeEach(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

for (const [name, page] of Object.entries(SCREENS)) {
  describe(`${name} — a trade-in refused with contact_merged is said in fr (KAN-382)`, () => {
    it('names the contact it was merged into', async () => {
      page.install(merged('cust-2'), [survivorRoute])
      page.render()
      await saveTradeIn()

      const expected = i18n.t('offerWorkspace.tradeIn.contactMergedInto', { name: 'Anna Muster', contactNumber: 'K-000002' })
      expect(await screen.findByText(expected)).toBeInTheDocument()
      expect(expected).toContain('Anna Muster (K-000002)')
      expect(screen.queryByText(i18n.t('offerWorkspace.tradeIn.saveError'))).toBeNull()
      expect(screen.queryByText(API_MESSAGE)).toBeNull()
    })

    it('says the contact was merged when the API names no survivor', async () => {
      page.install(merged(null))
      page.render()
      await saveTradeIn()

      expect(await screen.findByText(i18n.t('offerWorkspace.tradeIn.contactMerged'))).toBeInTheDocument()
      expect(screen.queryByText(API_MESSAGE)).toBeNull()
    })

    it('says the contact was merged when the survivor cannot be read', async () => {
      page.install(merged('cust-gone'))
      page.render()
      await saveTradeIn()

      expect(await screen.findByText(i18n.t('offerWorkspace.tradeIn.contactMerged'))).toBeInTheDocument()
    })
  })
}
