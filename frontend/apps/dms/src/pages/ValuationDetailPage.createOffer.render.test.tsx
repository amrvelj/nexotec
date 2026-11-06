// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes, useParams } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import type { ValuationRead } from '../api/types'
import { ValuationDetailPage } from './ValuationDetailPage'

// KAN-569 (ADR-061; Anto's answer A on KAN-528, then 1A and 2A): the valuation
// detail's alternative «Offerte aus dieser Bewertung erstellen» creates a
// draft offer, gives it the valuation's customer when there is one, takes the
// valuation over as its trade-in and opens the workspace. Disabled with its
// reason for an expired or used valuation; hidden for a session that may not
// write offers (KAN-430).

const VALUATION = {
  id: 'val-1', valuationNumber: 'B-000042', status: 'valid', source: 'manual', hasSignedContract: false,
  customerId: 'cust-1', customerLabel: 'Anna Muster', vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf',
  vehicleTrim: null, vehicleVin: null, usedAt: null, finalOffer: '12000.00', deductions: [], version: 3,
  validFrom: '2026-10-01', validUntil: '2026-12-31', vehicleFirstRegistration: null,
  createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
} as unknown as ValuationRead

const OFFER = { id: 'off-1', offerNumber: 'O-000017', version: 1, customerId: null, tradeInValuationId: null }

function session(writableCapabilities: string[], entitlements: string[] = ALL_ENTITLEMENTS): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Mia', lastName: 'Muster', email: 'mia@example.ch', accessRoles: ['sales'], isDealerManager: false },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements,
      writableCapabilities,
    }),
  }
}

function install(
  valuation: Partial<ValuationRead> = {},
  { capabilities = ALL_GATED_CAPABILITIES, entitlements = ALL_ENTITLEMENTS, refuseAttach = false } = {},
) {
  let attachCalls = 0
  return installFakeBackend([
    session(capabilities, entitlements),
    { method: 'GET', match: /^\/valuations\/val-1$/, handler: () => ({ ...VALUATION, ...valuation }) },
    { method: 'POST', match: /^\/sales\/offers$/, handler: () => OFFER },
    { method: 'GET', match: /^\/sales\/offers\/off-1$/, handler: () => ({ ...OFFER, customerId: 'cust-1', version: 2 }) },
    {
      method: 'PATCH',
      match: /^\/sales\/offers\/off-1$/,
      handler: () => ({ ...OFFER, customerId: 'cust-1', version: 2 }),
    },
    {
      method: 'POST',
      match: /^\/sales\/offers\/off-1\/trade-in\/valuation$/,
      handler: () => {
        attachCalls += 1
        if (refuseAttach && attachCalls === 1) {
          return status(409, { error: { code: 'conflict', message: 'Refused.', details: { reason: 'offer_has_contract', contractNumber: 'C-000003' } } })
        }
        return { ...OFFER, customerId: 'cust-1', tradeInValuationId: 'val-1', version: 3 }
      },
    },
  ])
}

function OfferWorkspaceStandIn() {
  const { id } = useParams<{ id: string }>()
  return <p>workspace {id}</p>
}

function renderPage() {
  renderWithProviders(
    <Routes>
      <Route path="/valuations/:id" element={<ValuationDetailPage />} />
      <Route path="/sales/offers/:id" element={<OfferWorkspaceStandIn />} />
    </Routes>,
    { route: '/valuations/val-1' },
  )
}

const actionName = () => i18n.t('valuationDetail.actions.createOffer')

async function headerAction() {
  await screen.findByText('B-000042')
  return screen.getByRole('button', { name: (n) => n.startsWith(actionName()) })
}

afterEach(() => cleanup())

describe('ValuationDetailPage — «Offerte aus dieser Bewertung erstellen» (KAN-569)', () => {
  it('stands beside «Neu bewerten» as the one alternative, and the overflow carries the same entry', async () => {
    const user = userEvent.setup()
    install()
    renderPage()

    const alternative = await headerAction()
    expect(alternative.hasAttribute('disabled')).toBe(false)
    expect(screen.getByRole('button', { name: i18n.t('valuationDetail.actions.revalue') })).toBeTruthy()

    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: new RegExp(actionName()) })).toBeTruthy()
  })

  it('creates the offer, gives it the valuation’s customer, takes the valuation over and opens the workspace', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderPage()

    await user.click(await headerAction())

    expect(await screen.findByText('workspace off-1')).toBeTruthy()
    const [create] = backend.callsTo(/^\/sales\/offers$/, 'POST')
    expect(create.headers.get('Idempotency-Key')).toBeTruthy()
    const [patch] = backend.callsTo(/^\/sales\/offers\/off-1$/, 'PATCH')
    expect(patch.body).toEqual({ customerId: 'cust-1' })
    expect(patch.headers.get('If-Match')).toBe('1')
    const [attach] = backend.callsTo(/\/trade-in\/valuation$/, 'POST')
    expect(attach.body).toEqual({ valuationId: 'val-1' })
    expect(attach.headers.get('If-Match')).toBe('2')
    expect(attach.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a double click starts one run: one offer is created', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderPage()

    await user.dblClick(await headerAction())

    expect(await screen.findByText('workspace off-1')).toBeTruthy()
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
  })

  it('a valuation without a customer gives an offer without a buyer (1A): no PATCH', async () => {
    const user = userEvent.setup()
    const backend = install({ customerId: null, customerLabel: null } as Partial<ValuationRead>)
    renderPage()

    await user.click(await headerAction())

    expect(await screen.findByText('workspace off-1')).toBeTruthy()
    expect(backend.callsTo(/^\/sales\/offers\/off-1$/, 'PATCH')).toHaveLength(0)
    const [attach] = backend.callsTo(/\/trade-in\/valuation$/, 'POST')
    expect(attach.headers.get('If-Match')).toBe('1')
  })

  it('a draft valuation may start an offer (2A)', async () => {
    install({ status: 'draft' } as Partial<ValuationRead>)
    renderPage()

    expect((await headerAction()).hasAttribute('disabled')).toBe(false)
  })

  it.each([
    ['expired', 'valuationDetail.actions.createOfferExpired'],
    ['used', 'valuationDetail.actions.createOfferUsed'],
  ] as const)('a %s valuation shows the action disabled with its reason, and sends nothing', async (state, reasonKey) => {
    const user = userEvent.setup()
    const backend = install({ status: state } as Partial<ValuationRead>)
    renderPage()

    const alternative = await headerAction()
    expect(alternative.hasAttribute('disabled')).toBe(true)
    await user.hover(alternative.parentElement ?? alternative)
    expect(await screen.findByText(i18n.t(reasonKey))).toBeTruthy()
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(0)
  })

  it('a Betrieb without the sales module sees it disabled with that reason', async () => {
    const user = userEvent.setup()
    install({}, { entitlements: ALL_ENTITLEMENTS.filter((e) => e !== 'sales') })
    renderPage()

    const alternative = await headerAction()
    expect(alternative.hasAttribute('disabled')).toBe(true)
    await user.hover(alternative.parentElement ?? alternative)
    expect(await screen.findByText(i18n.t('valuationDetail.actions.createOfferNoSales'))).toBeTruthy()
  })

  it('is not offered to a session that may not write offers (KAN-430)', async () => {
    const user = userEvent.setup()
    install({}, { capabilities: ALL_GATED_CAPABILITIES.filter((c) => c !== 'sales_offers') })
    renderPage()

    await screen.findByText('B-000042')
    expect(screen.queryByRole('button', { name: (n) => n.startsWith(actionName()) })).toBeNull()
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.markUsed')) })
    expect(screen.queryByRole('menuitem', { name: new RegExp(actionName()) })).toBeNull()
  })

  it('a refused trade-in step names the offer that exists, opens it, and a retry continues that offer', async () => {
    const user = userEvent.setup()
    const backend = install({}, { refuseAttach: true })
    renderPage()

    await user.click(await headerAction())

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('O-000017')
    expect(alert.textContent).toContain(i18n.t('offerWorkspace.errors.saveRefused.offerHasContract', { contractNumber: 'C-000003' }))
    expect(alert.textContent).not.toContain('MISSING')
    expect(screen.getByRole('button', { name: i18n.t('valuationDetail.errors.openCreatedOffer') })).toBeTruthy()

    // The retry re-reads the offer it made and only takes the valuation over.
    await user.click(await headerAction())
    expect(await screen.findByText('workspace off-1')).toBeTruthy()
    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
    expect(backend.callsTo(/^\/sales\/offers\/off-1$/, 'GET')).toHaveLength(1)
    expect(backend.callsTo(/^\/sales\/offers\/off-1$/, 'PATCH')).toHaveLength(1)
    expect(backend.callsTo(/\/trade-in\/valuation$/, 'POST')).toHaveLength(2)
  })
})
