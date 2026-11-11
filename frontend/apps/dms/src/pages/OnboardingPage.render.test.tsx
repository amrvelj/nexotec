// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { OnboardingPage } from './OnboardingPage'

// KAN-210 (D-A-06, FR-A-24/25): five steps — group, legal entity, modules,
// first manager, review — and one POST /v1/platform/onboarding; the success
// screen names the invitation; a UID already onboarded is said in the UI
// language.

const LEGAL_FORMS: FakeRoute = {
  method: 'GET',
  match: /\/reference-data\/legal_form$/,
  handler: () => ({
    items: [
      { id: 'lf-ag', listCode: 'legal_form', valueCode: 'ag', labelDe: 'Aktiengesellschaft (AG)', labelFr: 'SA', labelIt: 'SA', labelEn: 'AG', sortOrder: 0, active: true, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', createdBy: null, updatedBy: null },
    ],
    nextCursor: null,
  }),
}
const GROUPS: FakeRoute = { method: 'GET', match: /\/platform\/dealer-groups$/, handler: () => ({ items: [{ id: 'g-1', name: 'Emil Gruppe' }] }) }

async function fillAll(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByRole('textbox', { name: /Name der Gruppe/ }), 'Garage Muster Gruppe')
  await user.click(screen.getByRole('button', { name: 'Weiter' }))

  await user.type(screen.getByRole('textbox', { name: /Firma/ }), 'Garage Muster AG')
  await user.click(screen.getByRole('textbox', { name: /Markenbindung/ }))
  await user.click(await screen.findByRole('option', { name: 'Freier Händler' }))
  await user.type(screen.getByRole('textbox', { name: /^UID/ }), 'CHE-123.456.788')
  await user.type(screen.getByRole('textbox', { name: /Telefon/ }), '+41441234567')
  await user.type(screen.getByRole('textbox', { name: /Strasse/ }), 'Bahnhofstrasse')
  await user.type(screen.getByRole('textbox', { name: /PLZ/ }), '8001')
  await user.type(screen.getByRole('textbox', { name: /^Ort/ }), 'Zürich')
  await user.click(screen.getByRole('textbox', { name: /Kanton/ }))
  await user.click(await screen.findByRole('option', { name: /Zürich/ }))
  await user.click(screen.getByRole('button', { name: 'Weiter' }))

  // Modules: sales is ticked by default; add aftersales.
  expect(screen.getByRole('checkbox', { name: 'Verkauf' })).toBeChecked()
  await user.click(screen.getByRole('checkbox', { name: 'Aftersales' }))
  await user.click(screen.getByRole('button', { name: 'Weiter' }))

  await user.type(screen.getByRole('textbox', { name: /Vorname/ }), 'Martina')
  await user.type(screen.getByRole('textbox', { name: /Nachname/ }), 'Muster')
  await user.type(screen.getByRole('textbox', { name: /E-Mail/ }), 'martina@example.ch')
  await user.click(screen.getByRole('textbox', { name: /Funktion/ }))
  await user.click(await screen.findByRole('option', { name: 'Geschäftsführer' }))
  await user.click(screen.getByRole('textbox', { name: /Hauptrolle/ }))
  await user.click(await screen.findByRole('option', { name: 'Verkauf' }))
  await user.click(screen.getByRole('button', { name: 'Weiter' }))
}

describe('OnboardingPage (KAN-210)', () => {
  it('walks the five steps and onboards the Betrieb in one request', async () => {
    const backend = installFakeBackend([
      LEGAL_FORMS,
      GROUPS,
      {
        method: 'POST',
        match: /\/platform\/onboarding$/,
        handler: () =>
          status(201, {
            dealershipId: 'd-1',
            dealerGroupId: 'g-2',
            status: 'pending_onboarding',
            entitlements: ['aftersales', 'sales'],
            managerUserId: 'u-1',
            managerEmail: 'martina@example.ch',
            managerStatus: 'invited',
            locationId: 'l-1',
          }),
      },
    ])
    const user = userEvent.setup()
    renderWithProviders(<OnboardingPage />, { route: '/platform/onboarding' })

    // Next waits for the step's required fields.
    expect(await screen.findByRole('button', { name: 'Weiter' })).toBeDisabled()
    await fillAll(user)

    expect(screen.getByText('Garage Muster Gruppe')).toBeInTheDocument()
    expect(screen.getByText('Verkauf, Aftersales')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Betrieb aufnehmen' }))

    await waitFor(() => expect(backend.callsTo(/\/platform\/onboarding$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/platform\/onboarding$/, 'POST')[0].body).toEqual({
      group: { newGroupName: 'Garage Muster Gruppe' },
      legalEntity: {
        legalName: 'Garage Muster AG',
        tradeName: null,
        franchiseType: 'independent',
        legalForm: null,
        uid: 'CHE-123.456.788',
        address: { street: 'Bahnhofstrasse', houseNumber: null, postalCode: '8001', locality: 'Zürich', canton: 'ZH' },
        phone: '+41441234567',
      },
      entitlements: ['sales', 'aftersales'],
      manager: { firstName: 'Martina', lastName: 'Muster', email: 'martina@example.ch', role: 'gm', accessRoles: ['sales'] },
    })
    expect(await screen.findByTestId('onboarding-success')).toHaveTextContent('Garage Muster AG ist aufgenommen')
    expect(screen.getByText(/Die Einladung an martina@example.ch ist verschickt/)).toBeInTheDocument()
  })

  it('says a UID already onboarded in the UI language and keeps the entries', async () => {
    installFakeBackend([
      LEGAL_FORMS,
      GROUPS,
      {
        method: 'POST',
        match: /\/platform\/onboarding$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'A Betrieb with this UID is already onboarded.', details: { reason: 'uid_already_onboarded' } } }),
      },
    ])
    const user = userEvent.setup()
    renderWithProviders(<OnboardingPage />, { route: '/platform/onboarding' })
    await fillAll(user)
    await user.click(screen.getByRole('button', { name: 'Betrieb aufnehmen' }))

    expect(await screen.findByText('Ein Betrieb mit dieser UID ist bereits aufgenommen.')).toBeInTheDocument()
    expect(screen.queryByTestId('onboarding-success')).toBeNull()
    expect(screen.getByText('Garage Muster AG')).toBeInTheDocument()
  })
})
