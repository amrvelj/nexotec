// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../../test/fakeBackend'
import { ServiceOrderCreateDialog } from './ServiceOrderCreateDialog'
import { order } from '../test/serviceOrderFixtures'

// KAN-810 criterion 1 (D2): one search for plate, contact or VIN; a vehicle proposes its keeper as
// the ordering party; a contact offers its vehicles; the order is created with an Idempotency-Key;
// a «Gesperrt» contact is refused with the sentence why (the server's 409, check 2).

const VEHICLE = {
  id: 'vehicle-1', vin: 'W1KBMRB80MN6DTN3X', vehicleNumber: 'F-000123', stammnummer: null, typeApprovalNumber: null,
  firstRegistrationDate: null, catalogueVariantId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
  mergedIntoVehicleId: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
}
const EMPTY_PAGE = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const CONTACT = {
  id: 'contact-9', contactNumber: 'K-000009', contactType: 'private', firstName: 'Marta', lastName: 'Croci', companyName: null,
}

function install(extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [{ id: 'loc-1', name: 'Aarau' }] }) },
    {
      method: 'GET',
      match: /^\/vehicle-mdm\/search$/,
      handler: (req) =>
        req.params.get('q') === 'AG 525440'
          ? { resolved: { ...VEHICLE, currentPlate: 'AG 525440', currentPlateWithheld: false }, pickerCandidates: [], filtered: EMPTY_PAGE }
          : { resolved: null, pickerCandidates: [], filtered: EMPTY_PAGE },
    },
    {
      method: 'GET',
      match: /^\/contacts$/,
      handler: (req) => (req.params.get('q') === 'Croci' ? { ...EMPTY_PAGE, items: [CONTACT], total: 1 } : EMPTY_PAGE),
    },
    {
      method: 'GET',
      match: /^\/vehicle-mdm\/vehicle-1\/party-roles$/,
      handler: () => [
        { id: 'p-1', vehicleId: 'vehicle-1', contactId: 'contact-1', role: 'owner', effectiveFrom: '2025-01-01T00:00:00Z', effectiveTo: null, displayName: 'Leasing AG' },
        { id: 'p-2', vehicleId: 'vehicle-1', contactId: 'contact-1k', role: 'keeper', effectiveFrom: '2025-01-01T00:00:00Z', effectiveTo: null, displayName: 'Reto Wyss' },
      ],
    },
    {
      method: 'GET',
      match: /^\/contacts\/contact-9\/vehicles$/,
      handler: () => ({
        items: [
          {
            id: 'party-9', contactId: 'contact-9', vehicleId: 'vehicle-9', role: 'keeper', effectiveFrom: '2025-01-01T00:00:00Z', effectiveTo: null,
            vehicle: { id: 'vehicle-9', vin: 'TMBJJ7NS0K8000001', vehicleNumber: 'F-000009', make: 'Skoda', model: 'Kodiaq', modelYear: 2019 },
            otherParties: [], stockItem: null, createdAt: '2025-01-01T00:00:00Z', updatedAt: '2025-01-01T00:00:00Z',
          },
        ],
      }),
    },
  ])
}

function renderDialog() {
  const onCreated = vi.fn()
  renderWithProviders(<ServiceOrderCreateDialog opened onClose={() => {}} onCreated={onCreated} />)
  return { onCreated }
}

async function searchAndPick(user: ReturnType<typeof userEvent.setup>, query: string, rowText: RegExp) {
  const input = await screen.findByRole('textbox', { name: i18n.t('serviceOrders.create.searchLabel') })
  await user.type(input, query)
  await user.click(await screen.findByRole('option', { name: rowText }))
}

describe('«Neuer Auftrag» (KAN-810, D2)', () => {
  it('a vehicle found by its plate proposes its keeper; the order is posted with an Idempotency-Key', async () => {
    const user = userEvent.setup()
    const backend = install([{ method: 'POST', match: /^\/service-orders$/, handler: () => order() }])
    const { onCreated } = renderDialog()

    await searchAndPick(user, 'AG 525440', /AG 525440/)
    await waitFor(() => expect(screen.getByTestId('head-contact')).toHaveTextContent('Reto Wyss'))
    expect(screen.getByText(i18n.t('serviceOrders.create.holderProposed'))).toBeInTheDocument()
    // The invoice recipient is the ordering party until changed.
    expect(screen.getByTestId('head-invoiceRecipient')).toHaveTextContent('Reto Wyss')
    // One active location: nothing to choose.
    expect(screen.queryByRole('textbox', { name: i18n.t('serviceOrders.create.location') })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('serviceOrders.create.submit') }))
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    const [post] = backend.callsTo(/^\/service-orders$/, 'POST')
    expect(post.body).toEqual({ locationId: 'loc-1', contactId: 'contact-1k', vehicleId: 'vehicle-1' })
    expect(post.headers.get('Idempotency-Key')).toMatch(/[0-9a-f-]{36}/)
  })

  it('a «Gesperrt» contact is refused with the sentence why', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'POST',
        match: /^\/service-orders$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: 'Contact K-000001 is blocked (Betreibung offen)',
              details: { reason: 'service_order_contact_blocked', role: 'contact', contactNumber: 'K-000001', blockedReason: 'Betreibung offen' },
            },
          }),
      },
    ])
    const { onCreated } = renderDialog()

    await searchAndPick(user, 'AG 525440', /AG 525440/)
    await waitFor(() => expect(screen.getByTestId('head-contact')).toHaveTextContent('Reto Wyss'))
    await user.click(screen.getByRole('button', { name: i18n.t('serviceOrders.create.submit') }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('apiRefusal.reasons.service_order_contact_blocked', { reason: 'Betreibung offen' }))
    expect(alert).not.toHaveTextContent('Contact K-000001 is blocked')
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('a contact found by name offers its vehicles; the picked one is the order’s car', async () => {
    const user = userEvent.setup()
    const backend = install([{ method: 'POST', match: /^\/service-orders$/, handler: () => order() }])
    renderDialog()

    await searchAndPick(user, 'Croci', /Marta Croci/)
    expect(await screen.findByText(i18n.t('serviceOrders.create.contactVehicles', { name: 'Marta Croci' }))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('serviceOrders.create.createVehicle') })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Skoda Kodiaq/ }))

    expect(screen.getByTestId('head-vehicle')).toHaveTextContent('Skoda Kodiaq · TMBJJ7NS0K8000001')
    expect(screen.getByTestId('head-contact')).toHaveTextContent('Marta Croci')
    await user.click(screen.getByRole('button', { name: i18n.t('serviceOrders.create.submit') }))
    await waitFor(() => expect(backend.callsTo(/^\/service-orders$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/service-orders$/, 'POST')[0].body).toEqual({ locationId: 'loc-1', contactId: 'contact-9', vehicleId: 'vehicle-9' })
  })

  it('the submit is never held back: an incomplete order says what is missing and posts nothing', async () => {
    const user = userEvent.setup()
    const backend = install([{ method: 'POST', match: /^\/service-orders$/, handler: () => order() }])
    const { onCreated } = renderDialog()

    const submit = await screen.findByRole('button', { name: i18n.t('serviceOrders.create.submit') })
    expect(submit).toBeEnabled()
    await user.click(submit)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('serviceOrders.create.missing.vehicle'))
    expect(alert).toHaveTextContent(i18n.t('serviceOrders.create.missing.contact'))
    expect(backend.callsTo(/^\/service-orders$/, 'POST')).toHaveLength(0)
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('an ordering party chosen while the keeper is read is kept; the keeper is not proposed over it', async () => {
    const user = userEvent.setup()
    let release: (value: unknown) => void = () => {}
    const held = new Promise((resolve) => {
      release = resolve
    })
    install([
      { method: 'GET', match: /^\/vehicle-mdm\/vehicle-1\/party-roles$/, handler: () => held },
      { method: 'GET', match: /^\/contacts$/, handler: () => ({ ...EMPTY_PAGE, items: [CONTACT], total: 1 }) },
    ])
    renderDialog()

    await searchAndPick(user, 'AG 525440', /AG 525440/)
    await user.click(screen.getByRole('button', { name: i18n.t('serviceOrder.overview.changeTitle', { role: i18n.t('serviceOrder.overview.contact') }) }))
    await user.type(await screen.findByRole('textbox', { name: i18n.t('serviceOrder.overview.contactSearchLabel') }), 'Croci')
    await user.click(await screen.findByRole('option', { name: /Marta Croci/ }))
    expect(screen.getByTestId('head-contact')).toHaveTextContent('Marta Croci')

    release([{ id: 'p-2', vehicleId: 'vehicle-1', contactId: 'contact-1k', role: 'keeper', effectiveFrom: '2025-01-01T00:00:00Z', effectiveTo: null, displayName: 'Reto Wyss' }])
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getByTestId('head-contact')).toHaveTextContent('Marta Croci')
    expect(screen.queryByText(i18n.t('serviceOrders.create.holderProposed'))).not.toBeInTheDocument()
  })

  it('a keeper without a name is proposed as a contact without a name, never as a blank', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'GET',
        match: /^\/vehicle-mdm\/vehicle-1\/party-roles$/,
        handler: () => [{ id: 'p-2', vehicleId: 'vehicle-1', contactId: 'contact-1k', role: 'keeper', effectiveFrom: '2025-01-01T00:00:00Z', effectiveTo: null, displayName: null }],
      },
    ])
    renderDialog()

    await searchAndPick(user, 'AG 525440', /AG 525440/)
    await waitFor(() => expect(screen.getByTestId('head-contact')).toHaveTextContent(i18n.t('serviceOrders.create.unnamedContact')))
  })
})
