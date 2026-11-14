// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient } from '@tanstack/react-query'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { LetterheadPage } from './LetterheadPage'

// KAN-206 — Dealer Administration › Letterhead and template: the layout
// saves on every change (one PATCH each, with the version the last one
// returned) and the one DocumentPreview — the server renderer's PDF —
// renders again; the preview switches between the sample offer and invoice
// and the Betrieb or one of its locations; a location's address block and
// its own logo show for the location picked; a role that may not edit the
// template reads the settings and is offered no preview (it prints the IBAN).

const template = (overrides: Record<string, unknown> = {}) => ({
  id: 'template-1',
  dealershipId: 'dealership-1',
  headerNoteDe: null,
  headerNoteFr: null,
  headerNoteIt: null,
  headerNoteEn: null,
  headerLayout: 'logo_left',
  logoSize: 'm',
  addressWindow: 'right',
  footerColumns: ['company', 'contact', 'banking', 'register'],
  draftWatermark: true,
  pageNumbers: true,
  version: 4,
  createdAt: '2026-11-01T10:00:00Z',
  updatedAt: '2026-11-01T10:00:00Z',
  ...overrides,
})

const location = (overrides: Record<string, unknown> = {}) => ({
  id: 'location-1',
  dealershipId: 'dealership-1',
  name: 'Filiale Winterthur',
  code: 'WIN',
  isActive: true,
  letterheadAddressOverride: 'Filiale Winterthur\nIndustriestrasse 24\n8400 Winterthur',
  ...overrides,
})

const branding = {
  dealershipId: 'dealership-1',
  primaryColor: '#7C3AED',
  secondaryColor: null,
  placements: { documentHeader: true, priceTag: true, listing: true, emailSignature: true, appHeader: false },
  assets: [],
  locationLogos: [],
  contrast: { minimumRatio: 4.5, passes: true, pairs: [] },
  version: 7,
}

const pdf = () => new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), { headers: { 'content-type': 'application/pdf' } })

function routes(extra: FakeRoute[] = []): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1\/document-template$/, handler: () => template() },
    { method: 'GET', match: /\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [location()] }) },
    { method: 'GET', match: /\/dealerships\/dealership-1\/branding$/, handler: () => branding },
    { method: 'GET', match: /\/document-template\/preview$/, handler: pdf },
  ]
}

const { createObjectURL, revokeObjectURL } = URL
let urls = 0
beforeEach(() => {
  urls = 0
  URL.createObjectURL = vi.fn(() => `blob:fake-${++urls}`)
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = revokeObjectURL
})

const previewCalls = (backend: ReturnType<typeof installFakeBackend>) => backend.callsTo(/\/document-template\/preview$/, 'GET')

describe('LetterheadPage (KAN-206)', () => {
  it('shows the saved layout beside the renderer’s preview of a sample offer', async () => {
    const backend = installFakeBackend(routes())
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead' })

    expect(await screen.findByRole('heading', { name: 'Briefkopf und Vorlage' })).toBeInTheDocument()
    const letterhead = within(await screen.findByRole('region', { name: 'Briefkopf' }))
    expect(letterhead.getByRole('radio', { name: 'Links', checked: true })).toBeInTheDocument()
    expect(letterhead.getByRole('radio', { name: 'M · 40 mm' })).toBeChecked()
    expect(within(letterhead.getByRole('radiogroup', { name: 'Adressfenster' })).getByRole('radio', { name: 'Rechts' })).toBeChecked()
    const footer = within(letterhead.getByRole('group', { name: 'Fusszeile' }))
    expect(footer.getByRole('checkbox', { name: 'Firma und Adresse drucken' })).toBeDisabled() // always printed, A-27
    expect(footer.getByRole('checkbox', { name: 'Bankverbindung drucken' })).toBeChecked()
    expect(letterhead.getByRole('switch', { name: (n) => n.startsWith('«ENTWURF» auf Entwürfen') })).toBeChecked()

    // The preview is the one renderer's PDF in the kit's DocumentPreview.
    expect(await screen.findByTitle('Vorschau')).toHaveAttribute('src', expect.stringMatching(/^blob:fake-/))
    const [call] = previewCalls(backend)
    expect(call.params.get('document')).toBe('offer')
    expect(call.params.get('location')).toBeNull()
  })

  it('saves each change at once with If-Match and renders the preview again', async () => {
    const backend = installFakeBackend(
      routes([
        {
          method: 'PATCH',
          match: /\/document-template$/,
          handler: (request) => template({ ...(request.body as object), version: 5 }),
        },
      ]),
    )
    const user = userEvent.setup()
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead' })
    const letterhead = within(await screen.findByRole('region', { name: 'Briefkopf' }))
    await waitFor(() => expect(previewCalls(backend)).toHaveLength(1))

    await user.click(within(letterhead.getByRole('radiogroup', { name: 'Adressfenster' })).getByRole('radio', { name: 'Links' }))
    await waitFor(() => expect(backend.callsTo(/\/document-template$/, 'PATCH')).toHaveLength(1))
    const patch = backend.callsTo(/\/document-template$/, 'PATCH')[0]
    expect(patch.body).toEqual({ addressWindow: 'left' })
    expect(patch.headers.get('If-Match')).toBe('4')
    await waitFor(() => expect(previewCalls(backend)).toHaveLength(2))
    expect(await letterhead.findByRole('status')).toHaveTextContent('Gespeichert')
  })

  it('renders the preview afresh on coming back, after other screens changed what it prints (KAN-719)', async () => {
    // The app's own client (main.tsx: library defaults), shared across the
    // two visits as the shell shares it — not the test's gcTime-0 client.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const backend = installFakeBackend(routes())
    const first = renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead', queryClient })
    expect(await screen.findByTitle('Vorschau')).toHaveAttribute('src', 'blob:fake-1')
    await waitFor(() => expect(previewCalls(backend)).toHaveLength(1))

    // The manager leaves for the Business screen (its e-mail, website or a
    // location's address block change there; template and branding keep
    // their versions) and comes back.
    first.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fake-1')
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead', queryClient })

    await waitFor(() => expect(previewCalls(backend)).toHaveLength(2))
    expect(await screen.findByTitle('Vorschau')).toHaveAttribute('src', 'blob:fake-2')
    queryClient.clear()
  })

  it('moves and drops a footer column', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PATCH', match: /\/document-template$/, handler: (request) => template({ ...(request.body as object), version: 5 }) }]),
    )
    const user = userEvent.setup()
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead' })
    const footer = within(await screen.findByRole('group', { name: 'Fusszeile' }))

    await user.click(footer.getByRole('button', { name: 'Kontakt nach rechts' }))
    await waitFor(() => expect(backend.callsTo(/\/document-template$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/document-template$/, 'PATCH')[0].body).toEqual({
      footerColumns: ['company', 'banking', 'contact', 'register'],
    })

    await user.click(footer.getByRole('checkbox', { name: 'Handelsregister, UID und MWST drucken' }))
    await waitFor(() => expect(backend.callsTo(/\/document-template$/, 'PATCH')).toHaveLength(2))
    expect(backend.callsTo(/\/document-template$/, 'PATCH')[1].body).toEqual({
      footerColumns: ['company', 'banking', 'contact'],
    })
    expect(backend.callsTo(/\/document-template$/, 'PATCH')[1].headers.get('If-Match')).toBe('5')
  })

  it('previews the invoice for a location and shows that location’s letterhead', async () => {
    const backend = installFakeBackend(routes())
    const user = userEvent.setup()
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead' })
    await screen.findByTitle('Vorschau')

    await user.click(screen.getByRole('radio', { name: 'Rechnung' }))
    await user.click(screen.getByRole('textbox', { name: 'Gedruckt für' }))
    await user.click(await screen.findByRole('option', { name: 'Filiale Winterthur' }))

    await waitFor(() => {
      const last = previewCalls(backend).at(-1)!
      const params = last.params
      expect(params.get('document')).toBe('invoice')
      expect(params.get('location')).toBe('location-1')
    })
    const site = within(screen.getByRole('region', { name: 'Briefkopf eines Standorts' }))
    expect(site.getByText(/Industriestrasse 24/)).toBeInTheDocument()
    expect(site.getByText('Kein eigenes Logo – es wird das Logo des Betriebs gedruckt.')).toBeInTheDocument()
    expect(site.getByRole('button', { name: 'Hochladen' })).toBeDisabled()
  })

  it('offers a reader the settings read-only and no preview', async () => {
    const backend = installFakeBackend(
      routes([
        {
          method: 'GET',
          match: /\/auth\/me$/,
          handler: () => ({
            user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Test', lastName: 'Sales', email: 's@example.ch', accessRoles: ['sales'], isDealerManager: false, status: 'active', version: 1 },
            activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' },
            memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' }],
            entitlements: ['sales'],
            writableCapabilities: [],
          }),
        },
      ]),
    )
    renderWithProviders(<LetterheadPage />, { route: '/administration/letterhead' })
    expect(await screen.findByText('Nur die Geschäftsleitung ändert die Vorlage.')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Seitenzahlen («Seite 1 von 3»)' })).toBeDisabled()
    expect(screen.getByText(/steht nur der Geschäftsleitung zur Verfügung/)).toBeInTheDocument()
    expect(screen.queryByTitle('Vorschau')).toBeNull()
    expect(previewCalls(backend)).toHaveLength(0)
  })
})
