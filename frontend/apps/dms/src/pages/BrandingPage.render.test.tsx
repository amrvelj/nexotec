// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import de from '../i18n/locales/de'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { BrandingPage } from './BrandingPage'

// KAN-205 — Dealer Administration › Logo and colours: the three sections
// render from the branding record beside the preview the renderer made; the
// contrast verdict follows the colour while it is picked and the save says
// when it went through with a warning; an upload sends the file itself with
// the record's If-Match and a refusal says why in the UI language; a role
// without the `branding` write is offered no upload and no save.

const branding = (overrides: Record<string, unknown> = {}) => ({
  dealershipId: 'dealership-1',
  primaryColor: '#7C3AED',
  secondaryColor: null,
  placements: { documentHeader: true, priceTag: true, listing: true, emailSignature: true, appHeader: false },
  assets: [
    { kind: 'primary', source: 'own', mimeType: 'image/png', width: 1200, height: 400, byteSize: 2048, uploadedBy: null, uploadedAt: '2026-10-31T10:00:00Z' },
    { kind: 'mono_dark', source: 'primary', mimeType: null, width: null, height: null, byteSize: null, uploadedBy: null, uploadedAt: null },
    { kind: 'favicon', source: 'primary', mimeType: null, width: null, height: null, byteSize: null, uploadedBy: null, uploadedAt: null },
  ],
  contrast: {
    minimumRatio: 4.5,
    passes: true,
    pairs: [{ pair: 'primary_on_white', foreground: '#7C3AED', background: '#FFFFFF', ratio: 5.7, passes: true }],
  },
  version: 3,
  ...overrides,
})

const binary = (type: string) => () => new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), { headers: { 'content-type': type } })

function routes(extra: FakeRoute[] = []): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1\/branding$/, handler: () => branding() },
    { method: 'GET', match: /\/branding\/assets\/[a-z_]+$/, handler: binary('image/png') },
    { method: 'GET', match: /\/branding\/preview$/, handler: binary('application/pdf') },
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

describe('BrandingPage (KAN-205)', () => {
  it('renders the logo variants, the colours and the placements beside the preview', async () => {
    const backend = installFakeBackend(routes())
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })

    expect(await screen.findByRole('heading', { name: 'Logo' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Farben' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Wo das Logo erscheint' })).toBeInTheDocument()

    const primary = within(screen.getByRole('group', { name: 'Hauptlogo' }))
    expect(primary.getByText("PNG · 1'200 × 400 px")).toBeInTheDocument()
    expect(await primary.findByRole('img', { name: 'Hauptlogo' })).toHaveAttribute('src', expect.stringMatching(/^blob:fake-/))
    expect(within(screen.getByRole('group', { name: 'Favicon' })).getByText('Verwendet das Hauptlogo')).toBeInTheDocument()

    const verdict = within(screen.getByRole('list', { name: 'Farben' }))
    expect(verdict.getByText('Primärfarbe und Weiss')).toBeInTheDocument()
    expect(verdict.getByText('5.70 : 1')).toBeInTheDocument()
    expect(verdict.getByText('AA erfüllt')).toBeInTheDocument()

    expect(screen.getByRole('switch', { name: 'Briefkopf der Dokumente' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'Kopfzeile der Anwendung' })).not.toBeChecked()

    // The preview is the renderer's PDF, in the kit's DocumentPreview.
    expect(await screen.findByTitle('Vorschau des Briefkopfs')).toHaveAttribute('src', expect.stringMatching(/^blob:fake-/))
    expect(backend.callsTo(/\/branding\/preview$/, 'GET')).toHaveLength(1)
  })

  it('judges the contrast while the colour is picked and warns when a failing save went through', async () => {
    const saved = branding({
      primaryColor: '#FACC15',
      version: 4,
      contrast: {
        minimumRatio: 4.5,
        passes: false,
        pairs: [{ pair: 'primary_on_white', foreground: '#FACC15', background: '#FFFFFF', ratio: 1.53, passes: false }],
      },
    })
    const backend = installFakeBackend(routes([{ method: 'PATCH', match: /\/branding$/, handler: () => saved }]))
    const user = userEvent.setup()
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    const colours = within(await screen.findByRole('region', { name: 'Farben' }))

    const input = colours.getByRole('textbox', { name: (n) => n.startsWith('Primärfarbe') })
    await user.clear(input)
    await user.type(input, '#FACC15')
    const verdict = within(colours.getByRole('list', { name: 'Farben' }))
    expect(await verdict.findByText('1.53 : 1')).toBeInTheDocument()
    expect(verdict.getByText('Zu wenig Kontrast')).toBeInTheDocument()

    await user.click(colours.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/branding$/, 'PATCH')).toHaveLength(1))
    const call = backend.callsTo(/\/branding$/, 'PATCH')[0]
    expect(call.body).toEqual({ primaryColor: '#FACC15' })
    expect(call.headers.get('If-Match')).toBe('3')
    expect(await colours.findByText(de.branding.colours.savedWithWarning)).toBeInTheDocument()
    // The saved record moved the version on: the preview renders again.
    await waitFor(() => expect(backend.callsTo(/\/branding\/preview$/, 'GET')).toHaveLength(2))
  })

  it('uploads the file itself with If-Match and says a refusal in the UI language', async () => {
    const backend = installFakeBackend(
      routes([
        {
          method: 'PUT',
          match: /\/branding\/assets\/primary$/,
          handler: () => status(422, { error: { code: 'unprocessable_entity', message: 'JPEG is not accepted…', details: { reason: 'logo_format_jpeg' } } }),
        },
      ]),
    )
    // The picker offers SVG and PNG; a manager who switches it to "all files"
    // and picks a JPEG anyway is told why by the API.
    const user = userEvent.setup({ applyAccept: false })
    const { container } = renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    await screen.findByRole('group', { name: 'Hauptlogo' })
    const group = screen.getByRole('group', { name: 'Hauptlogo' })
    const fileInput = group.querySelector('input[type="file"]') as HTMLInputElement
    expect(container.contains(fileInput)).toBe(true)
    const photo = new File(['jpeg'], 'logo.jpg', { type: 'image/jpeg' })
    await user.upload(fileInput, photo)
    await user.click(within(group).getByRole('button', { name: 'Hochladen' }))

    await waitFor(() => expect(backend.callsTo(/\/branding\/assets\/primary$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/\/branding\/assets\/primary$/, 'PUT')[0]
    expect(call.body).toBe(photo)
    expect(call.headers.get('If-Match')).toBe('3')
    expect(call.headers.get('Content-Type')).toBe('image/jpeg')
    expect(await within(group).findByRole('alert')).toHaveTextContent(de.apiRefusal.reasons.logo_format_jpeg)
  })

  it('names the size of a PNG that is too small', async () => {
    installFakeBackend(
      routes([
        {
          method: 'PUT',
          match: /\/branding\/assets\/favicon$/,
          handler: () =>
            status(422, {
              error: { code: 'unprocessable_entity', message: '…', details: { reason: 'logo_too_small', width: 400, height: 120, minLongEdge: 600 } },
            }),
        },
      ]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    const group = await screen.findByRole('group', { name: 'Favicon' })
    await user.upload(group.querySelector('input[type="file"]') as HTMLInputElement, new File(['png'], 'icon.png', { type: 'image/png' }))
    await user.click(within(group).getByRole('button', { name: 'Hochladen' }))
    const alert = await within(group).findByRole('alert')
    expect(alert).toHaveTextContent('Dieses PNG ist 400 × 120 px gross')
    expect(alert).toHaveTextContent('50 mm')
    expect(alert).toHaveTextContent('600 px')
  })

  it('names the size of a PNG with too many pixels (KAN-205 review)', async () => {
    installFakeBackend(
      routes([
        {
          method: 'PUT',
          match: /\/branding\/assets\/primary$/,
          handler: () =>
            status(422, {
              error: {
                code: 'unprocessable_entity',
                message: '…',
                details: { reason: 'logo_too_many_pixels', width: 10000, height: 10000, maxMegapixels: 16 },
              },
            }),
        },
      ]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    const group = await screen.findByRole('group', { name: 'Hauptlogo' })
    await user.upload(group.querySelector('input[type="file"]') as HTMLInputElement, new File(['png'], 'huge.png', { type: 'image/png' }))
    await user.click(within(group).getByRole('button', { name: 'Hochladen' }))
    const alert = await within(group).findByRole('alert')
    expect(alert).toHaveTextContent("Dieses PNG hat 10'000 × 10'000 px – mehr als 16 Megapixel")
  })

  it('saves only the placement that changed', async () => {
    const backend = installFakeBackend(routes([{ method: 'PATCH', match: /\/branding$/, handler: () => branding({ version: 4 }) }]))
    const user = userEvent.setup()
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    const placements = within(await screen.findByRole('region', { name: 'Wo das Logo erscheint' }))
    await user.click(placements.getByRole('switch', { name: 'Preisschild' }))
    await user.click(placements.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/branding$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/branding$/, 'PATCH')[0].body).toEqual({ placements: { priceTag: false } })
  })

  it('offers no upload and no save to a role without the branding write', async () => {
    installFakeBackend(
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
    renderWithProviders(<BrandingPage />, { route: '/administration/branding' })
    expect(await screen.findByRole('group', { name: 'Hauptlogo' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Hochladen' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
    expect(screen.getByRole('switch', { name: 'Preisschild' })).toBeDisabled()
  })
})
