// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { PublishingPage } from './PublishingPage'
import { WebsiteListingCard } from '../components/stock-detail/WebsiteListingCard'

// KAN-225 — Dealer Administration › Publishing: the Betrieb's sections and
// the validation panel in the dealer's words; a save sends only what changed
// with the screen's one version as If-Match; a location switches its own
// override on and then edits only it; its lead routing and photos; and the
// stock detail's website card lists what refuses the car.

const empty = {}
const profile = (overrides: Record<string, unknown> = {}) => ({
  dealershipId: 'dealership-1',
  tradeName: 'Garage Muster',
  website: 'https://garage-muster.ch',
  websiteDomain: null,
  isPublished: false,
  socialProfiles: {},
  shortDescription: { de: 'Ihr Garagist' },
  longDescription: empty,
  impressum: empty,
  privacyNotice: empty,
  withdrawalWording: empty,
  agbUrl: null,
  agbDocument: null,
  warrantyText: empty,
  mfkStatement: empty,
  priceDisplayWording: empty,
  standardEquipmentBlurb: empty,
  tradeInPromotionText: empty,
  deliveryFeeChf: null,
  financingTermMonths: null,
  financingDownPaymentChf: null,
  financingResidualValueChf: null,
  financingNominalRatePct: null,
  financingEffectiveRatePct: null,
  financingProvider: null,
  locationOverrides: [],
  leadRouting: [
    { locationId: 'loc-1', locationName: 'Zürich Nord', leadEmail: null, leadPhone: null, createsTask: false, recipientUserId: null, recipientRole: null },
  ],
  images: [],
  version: 4,
  updatedAt: '2026-11-14T10:00:00Z',
  ...overrides,
})

const validation = {
  channel: 'website',
  ready: false,
  missing: [
    { code: 'impressum_missing', section: 'legal', locationId: null, locationName: null },
    { code: 'lead_email_missing', section: 'leadRouting', locationId: 'loc-1', locationName: 'Zürich Nord' },
  ],
}

function routes(extra: FakeRoute[] = []): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1\/publishing-profile$/, handler: () => profile() },
    { method: 'GET', match: /\/publishing-profile\/validation$/, handler: () => validation },
    { method: 'GET', match: /\/dealerships\/dealership-1\/public-stock$/, handler: () => ({ dealershipId: 'dealership-1', total: 2, items: [] }) },
  ]
}

const { createObjectURL } = URL
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:fake')
})
afterEach(() => {
  URL.createObjectURL = createObjectURL
})

describe('PublishingPage (KAN-225)', () => {
  it('shows the sections and says what the website still needs', async () => {
    installFakeBackend(routes())
    renderWithProviders(<PublishingPage />, { route: '/administration/publishing' })

    expect(await screen.findByRole('heading', { name: 'Öffentlicher Auftritt' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Rechtliche Angaben' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Inseratsvorgaben' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Finanzierungsbeispiel' })).toBeInTheDocument()
    expect(screen.getByText('Wortlaut in rechtlicher Prüfung (A-6)')).toBeInTheDocument()
    expect(screen.getByText('Öffentlicher Name: Garage Muster')).toBeInTheDocument()

    const panel = within(screen.getByRole('region', { name: 'Bereit für die Website?' }))
    expect(await panel.findByText('Das Impressum in der Korrespondenzsprache des Betriebs')).toBeInTheDocument()
    expect(panel.getByText(/Eine E-Mail für Anfragen am Standort Zürich Nord/)).toBeInTheDocument()
    expect(await panel.findByText('2 Fahrzeuge sind auf der Website.')).toBeInTheDocument()
  })

  it('saves only what changed with the screen version as If-Match', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PATCH', match: /\/publishing-profile$/, handler: () => profile({ websiteDomain: 'garage-muster.ch', version: 5 }) }]),
    )
    const user = userEvent.setup()
    renderWithProviders(<PublishingPage />, { route: '/administration/publishing' })

    const domain = await screen.findByRole('textbox', { name: (n) => n.startsWith('Domain der Website') })
    await user.type(domain, 'garage-muster.ch')
    const section = screen.getByRole('heading', { name: 'Öffentlicher Auftritt' }).closest('.mantine-Card-root') as HTMLElement
    await user.click(within(section).getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/publishing-profile$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/\/publishing-profile$/, 'PATCH')
    expect(call.body).toEqual({ websiteDomain: 'garage-muster.ch' })
    expect(call.headers.get('If-Match')).toBe('4')
  })

  it("switches a location's override on and shows its lead routing and photos", async () => {
    const withOverride = profile({
      version: 5,
      locationOverrides: [
        {
          locationId: 'loc-1',
          shortDescription: {},
          longDescription: {},
          warrantyText: {},
          mfkStatement: {},
          priceDisplayWording: {},
          standardEquipmentBlurb: {},
          tradeInPromotionText: {},
          deliveryFeeChf: null,
          financingTermMonths: null,
          financingDownPaymentChf: null,
          financingResidualValueChf: null,
          financingNominalRatePct: null,
          financingEffectiveRatePct: null,
          financingProvider: null,
        },
      ],
    })
    const backend = installFakeBackend(routes([{ method: 'POST', match: /\/publishing-profile\/locations\/loc-1$/, handler: () => withOverride }]))
    const user = userEvent.setup()
    renderWithProviders(<PublishingPage />, { route: '/administration/publishing' })

    await user.click(await screen.findByRole('textbox', { name: 'Angaben für' }))
    await user.click(await screen.findByRole('option', { name: 'Zürich Nord' }))
    expect(await screen.findByRole('heading', { name: 'Anfragen' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fotos' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Rechtliche Angaben' })).not.toBeInTheDocument()
    expect(screen.getByText(/übernimmt Beschreibung, Inseratsvorgaben/)).toBeInTheDocument()

    await user.click(screen.getByRole('switch', { name: 'Eigene Angaben für diesen Standort' }))
    await waitFor(() => expect(backend.callsTo(/\/publishing-profile\/locations\/loc-1$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/publishing-profile\/locations\/loc-1$/, 'POST')[0].headers.get('If-Match')).toBe('4')
    expect(await screen.findByRole('heading', { name: 'Öffentlicher Auftritt' })).toBeInTheDocument()
  })
})

describe('WebsiteListingCard (KAN-225)', () => {
  it('lists what the publishing profile lacks and links to the Publishing screen', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /\/inventory\/stock-items\/si-1\/website-listing$/,
        handler: () => ({
          stockItemId: 'si-1',
          isListed: false,
          listedAt: null,
          unlistedAt: null,
          version: 1,
          blockingConditions: [
            { field: 'Preis', message: 'Kein Effektivpreis hinterlegt.', code: null },
            { field: 'publishingProfile', message: '', code: 'impressum_missing' },
          ],
        }),
      },
    ])
    renderWithProviders(<WebsiteListingCard stockItemId="si-1" canPublish />)
    expect(await screen.findByText('Nicht auf der Website')).toBeInTheDocument()
    expect(screen.queryByText(/Kein Effektivpreis/)).not.toBeInTheDocument() // the tab's banner names it
    expect(screen.getByText('Das Impressum in der Korrespondenzsprache des Betriebs')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Angaben unter Veröffentlichung ergänzen' })).toHaveAttribute('href', '/administration/publishing')
    expect(screen.getByRole('button', { name: 'Auf die Website stellen' })).toBeDisabled()
  })

  it('puts a ready car on the website with If-Match', async () => {
    const read = (isListed: boolean, version: number) => ({ stockItemId: 'si-1', isListed, listedAt: null, unlistedAt: null, version, blockingConditions: [] })
    const backend = installFakeBackend([
      { method: 'GET', match: /\/website-listing$/, handler: () => read(false, 1) },
      { method: 'POST', match: /\/website-listing\/publish$/, handler: () => read(true, 2) },
    ])
    const user = userEvent.setup()
    renderWithProviders(<WebsiteListingCard stockItemId="si-1" canPublish />)
    await user.click(await screen.findByRole('button', { name: 'Auf die Website stellen' }))
    expect(await screen.findByText('Auf der Website')).toBeInTheDocument()
    expect(backend.callsTo(/\/website-listing\/publish$/, 'POST')[0].headers.get('If-Match')).toBe('1')
  })
})
