// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { AuditEventRead } from '../../api/types'
import { HistoryTab } from './HistoryTab'

// KAN-336 — the History tab interpolated the raw audit verb into its headline
// ("Vous create ce client") and showed humanised English snake_case keys
// ("Vat registered", "Advisor id") and stored enum values (`frau`, `prospect`)
// in every UI language. Expectations are hardcoded French, never derived via
// i18n.t(), so they cannot be computed the way the component computes them.

function event(over: Partial<AuditEventRead>): AuditEventRead {
  return {
    id: 'ev-1',
    entityType: 'customer',
    entityId: 'c1',
    tenantId: 'group-1',
    action: 'update',
    actorId: null,
    before: null,
    after: null,
    reason: null,
    createdAt: '2026-10-01T10:00:00Z',
    ...over,
  }
}

const ADVISOR_ID = '0192f0a0-0000-7000-8000-00000000a001'
const VEHICLE_ID = '0192f0a0-0000-7000-8000-00000000b001'
const DEALERSHIP_ID = '0192f0a0-0000-7000-8000-00000000c001'

function renderHistory(
  events: AuditEventRead[],
  idLabels: Record<string, string> = {},
  countryLabels: Record<string, string> = {},
) {
  installFakeBackend([])
  return renderWithProviders(
    <HistoryTab
      events={events}
      loading={false}
      error={null}
      locale="fr-CH"
      idLabels={idLabels}
      countryLabels={countryLabels}
    />,
  )
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('HistoryTab in French (KAN-336)', () => {
  it('a create event has a translated headline, Overview labels and translated values', async () => {
    await i18n.changeLanguage('fr')
    renderHistory(
      [
        event({
          action: 'create',
          after: {
            first_name: 'Hans',
            salutation: 'frau',
            gender: 'unspecified',
            lifecycle_status: 'prospect',
            language: 'it',
            advisor_id: ADVISOR_ID,
            dealership_id: DEALERSHIP_ID,
          },
        }),
      ],
      { [ADVISOR_ID]: 'Anna Muster' },
    )

    expect(screen.getByText(/Client créé/)).toBeInTheDocument()
    expect(screen.queryByText(/create/)).not.toBeInTheDocument()
    expect(screen.queryByText(/ce client/)).not.toBeInTheDocument()

    expect(screen.getByText('Prénom')).toBeInTheDocument()
    expect(screen.getByText('Civilité')).toBeInTheDocument()
    expect(screen.getByText(/— → Madame/)).toBeInTheDocument()
    expect(screen.getByText('Genre')).toBeInTheDocument()
    expect(screen.getByText(/— → Non précisé/)).toBeInTheDocument()
    expect(screen.getByText('Statut du cycle de vie')).toBeInTheDocument()
    expect(screen.getByText(/— → Prospect/)).toBeInTheDocument()
    expect(screen.getByText('Langue de correspondance')).toBeInTheDocument()
    expect(screen.getByText(/— → Italien/)).toBeInTheDocument()
    // The advisor id renders the label the customer record carries.
    expect(screen.getByText('Conseiller')).toBeInTheDocument()
    expect(screen.getByText(/— → Anna Muster/)).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(ADVISOR_ID))).not.toBeInTheDocument()
    // KAN-371: an id nothing names reads as a translated marker under a
    // translated field name, never as a bare UUID.
    expect(screen.getByText('Entreprise')).toBeInTheDocument()
    expect(screen.getByText(/— → enregistrement inconnu/)).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(DEALERSHIP_ID))).not.toBeInTheDocument()

    for (const english of ['First name', 'Salutation', 'Lifecycle status', 'Advisor id', 'Dealership id']) {
      expect(screen.queryByText(english)).not.toBeInTheDocument()
    }
    expect(screen.queryByText(/frau|unspecified|prospect/)).not.toBeInTheDocument()
  })

  it('an update event has a translated headline, Overview labels and translated values', async () => {
    await i18n.changeLanguage('fr')
    renderHistory([
      event({
        action: 'update',
        before: { vat_registered: false, preferred_channel: 'email', payment_terms: null, customer_since: null },
        after: { vat_registered: true, preferred_channel: 'phone', payment_terms: 'net_30', customer_since: '2026-01-31' },
      }),
    ])

    expect(screen.getByText(/Client modifié/)).toBeInTheDocument()
    expect(screen.queryByText(/update/)).not.toBeInTheDocument()

    expect(screen.getByText('Assujetti à la TVA')).toBeInTheDocument()
    expect(screen.getByText(/Non → Oui/)).toBeInTheDocument()
    expect(screen.getByText('Canal préféré')).toBeInTheDocument()
    expect(screen.getByText(/E-mail → Téléphone/)).toBeInTheDocument()
    expect(screen.getByText('Conditions de paiement')).toBeInTheDocument()
    expect(screen.getByText(/— → Net à 30 jours/)).toBeInTheDocument()
    expect(screen.getByText('Client depuis')).toBeInTheDocument()
    expect(screen.getByText(/— → 31\.01\.2026/)).toBeInTheDocument()

    expect(screen.queryByText('Vat registered')).not.toBeInTheDocument()
    expect(screen.queryByText(/net_30/)).not.toBeInTheDocument()
  })

  it('nationality and an address country render the country name, as on the Overview', async () => {
    await i18n.changeLanguage('fr')
    renderHistory(
      [
        event({ action: 'update', before: { nationality: 'CH' }, after: { nationality: 'FR' } }),
        event({ id: 'ev-2', action: 'address_add', after: { addressType: 'domicile', addressCountry: 'CH' } }),
      ],
      {},
      { CH: 'Suisse', FR: 'France' },
    )

    expect(screen.getByText('Nationalité')).toBeInTheDocument()
    expect(screen.getByText(/Suisse → France/)).toBeInTheDocument()
    expect(screen.getByText(/— → Suisse/)).toBeInTheDocument()
    expect(screen.queryByText(/CH → FR|— → CH/)).not.toBeInTheDocument()
  })

  it('a vehicle-party event names the vehicle and translates the role', async () => {
    await i18n.changeLanguage('fr')
    renderHistory(
      [
        event({
          action: 'vehicle_party_add',
          after: { vehicleId: VEHICLE_ID, role: 'keeper', effectiveFrom: '2026-03-05T12:00:00+00:00', effectiveTo: null },
        }),
      ],
      { [VEHICLE_ID]: 'VW Golf (2019)' },
    )

    expect(screen.getByText(/Véhicule lié/)).toBeInTheDocument()
    expect(screen.getByText('Véhicule')).toBeInTheDocument()
    expect(screen.getByText(/— → VW Golf \(2019\)/)).toBeInTheDocument()
    expect(screen.getByText('Rôle')).toBeInTheDocument()
    expect(screen.getByText(/— → Détenteur/)).toBeInTheDocument()
    expect(screen.getByText('Depuis')).toBeInTheDocument()
    expect(screen.queryByText(/vehicle_party_add|keeper/)).not.toBeInTheDocument()
  })

  it('a contact-channel event translates its type, consent and primary flag', async () => {
    await i18n.changeLanguage('fr')
    renderHistory([
      event({
        action: 'phone_add',
        after: {
          phoneType: 'mobile',
          phoneE164: '+41791234567',
          isPrimary: true,
          consentGranted: true,
          consentScope: 'marketing',
          consentSource: 'counter',
        },
      }),
    ])

    expect(screen.getByText(/Téléphone ajouté/)).toBeInTheDocument()
    expect(screen.getByText('Type de téléphone')).toBeInTheDocument()
    expect(screen.getByText(/— → Mobile/)).toBeInTheDocument()
    expect(screen.getByText('Numéro de téléphone')).toBeInTheDocument()
    expect(screen.getByText(/— → \+41791234567/)).toBeInTheDocument()
    expect(screen.getByText('Source du consentement')).toBeInTheDocument()
    expect(screen.getByText(/— → Guichet/)).toBeInTheDocument()
    expect(screen.queryByText(/Phone e164|counter/)).not.toBeInTheDocument()
  })

  it('an action the tab does not know gets a translated generic headline, never the raw verb', async () => {
    await i18n.changeLanguage('fr')
    renderHistory([event({ action: 'anonymise', after: { first_name: '***' } })])
    expect(screen.getByText(/Modification enregistrée/)).toBeInTheDocument()
    expect(screen.queryByText(/anonymise/)).not.toBeInTheDocument()
  })

  it('a field the tab does not know is shown under its key as recorded, never a missing-key marker', async () => {
    await i18n.changeLanguage('fr')
    renderHistory([event({ before: { some_new_field: 'a' }, after: { some_new_field: 'b' } })])
    expect(screen.getByText('some_new_field')).toBeInTheDocument()
    expect(screen.queryByText(/MISSING I18N KEY/)).not.toBeInTheDocument()
  })
})

// KAN-371 (Anto, option A) — the History endpoint names the ids its events
// carry; the tab shows the Betrieb, the customer a record was merged into and
// the acting user by name, in the UI language's frame.
describe('HistoryTab — ids read as names (KAN-371)', () => {
  it('renders the Betrieb, the merged-into customer and the actor by name, in fr', async () => {
    await i18n.changeLanguage('fr')
    const SURVIVOR_ID = '01a1487e-0000-7000-8000-000000000099'
    const ACTOR_ID = '01a1487e-0000-7000-8000-000000000077'
    renderHistory(
      [
        event({ action: 'create', after: { first_name: 'Hans', dealership_id: DEALERSHIP_ID } }),
        event({
          action: 'merge',
          actorId: ACTOR_ID,
          before: { lifecycleStatus: 'prospect', duplicateOfCustomerId: null },
          after: { lifecycleStatus: 'merged', duplicateOfCustomerId: SURVIVOR_ID },
        }),
      ],
      { [DEALERSHIP_ID]: 'Garage Muster AG', [SURVIVOR_ID]: 'Tina Treu (K-000042)', [ACTOR_ID]: 'Bea Berater' },
    )

    expect(screen.getByText(/— → Garage Muster AG/)).toBeInTheDocument()
    expect(screen.getByText(/— → Tina Treu \(K-000042\)/)).toBeInTheDocument()
    expect(screen.getByText('Bea Berater')).toBeInTheDocument()
    for (const id of [DEALERSHIP_ID, SURVIVOR_ID, ACTOR_ID]) {
      expect(screen.queryByText(new RegExp(id))).not.toBeInTheDocument()
    }
  })
})
