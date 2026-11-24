// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { BusinessProfileLinkCallbackPage } from '../BusinessProfileLinkCallbackPage'
import { BusinessProfileSection } from './BusinessProfileSection'
import { businessProfileNavigation } from './businessProfileLink'

// KAN-226 PR-4 — the Google Business Profile section of Administration ›
// Integrationen: a card per active location with its link to Google and what
// the nightly drift check found, and Google's callback page.

const site = (id: string, name: string, isActive = true) => ({ id, name, isActive })

const binding = (overrides: Record<string, unknown> = {}) => ({
  id: 'b-1',
  locationId: 'loc-1',
  locationLabel: 'Zürich',
  status: 'active',
  googleAccountName: 'accounts/1',
  googleLocationName: 'locations/1',
  googleLocationTitle: 'Garage Beispiel Zürich',
  errorCode: null,
  connectedAt: '2026-11-20T10:00:00Z',
  version: 3,
  drift: null,
  unmappedDepartments: [],
  ...overrides,
})

function routes(bindings: unknown[], extra: FakeRoute[] = [], configured = true): FakeRoute[] {
  return [
    { method: 'GET', match: /^\/dealerships\/dealership-1\/business-profile\/bindings$/, handler: () => ({ items: bindings, configured }) },
    {
      method: 'GET',
      match: /^\/dealerships\/dealership-1\/locations$/,
      handler: () => ({ items: [site('loc-1', 'Zürich'), site('loc-2', 'Winterthur'), site('loc-3', 'Alt', false)] }),
    },
    ...extra,
  ]
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Google Business Profile section (KAN-226 PR-4)', () => {
  it('names the fields that differ at Google and the departments Google does not show', async () => {
    installFakeBackend(
      routes([
        binding({
          drift: { checkedAt: '2026-11-22T02:00:00Z', outcome: 'drift', driftedFields: ['phone', 'regularHours'], reason: null },
          unmappedDepartments: ['parts'],
        }),
      ])
    )
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText('Garage Beispiel Zürich')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.drift.drift', { fields: 'Telefon, Öffnungszeiten', date: '22.11.2026' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.drift.hint'))).toBeInTheDocument()
    expect(screen.getByText('Teile')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.unmappedHint'))).toBeInTheDocument()
    // A deactivated location without a link gets no card.
    expect(screen.queryByText('Alt')).not.toBeInTheDocument()
    expect(screen.getByText('Winterthur')).toBeInTheDocument()
  })

  it('says when a location is in sync and when it was never checked', async () => {
    installFakeBackend(
      routes([
        binding({ drift: { checkedAt: '2026-11-22T02:00:00Z', outcome: 'in_sync', driftedFields: [], reason: null } }),
        binding({ id: 'b-2', locationId: 'loc-2', locationLabel: 'Winterthur', googleLocationTitle: 'Garage Winterthur' }),
      ])
    )
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText(i18n.t('businessProfile.card.drift.in_sync', { date: '22.11.2026' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.drift.never'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('businessProfile.card.drift.hint'))).not.toBeInTheDocument()
  })

  it('a manager connects a location: Google’s sign-in opens', async () => {
    const user = userEvent.setup()
    const toGoogle = vi.spyOn(businessProfileNavigation, 'toGoogle').mockImplementation(() => {})
    const backend = installFakeBackend(
      routes([], [
        { method: 'POST', match: /\/business-profile\/links$/, handler: () => status(201, { authorizeUrl: 'https://accounts.google.com/o/oauth2/v2/auth?state=s' }) },
      ])
    )
    renderWithProviders(<BusinessProfileSection />)

    await screen.findByText('Winterthur')
    const [first] = screen.getAllByRole('button', { name: i18n.t('businessProfile.card.actions.connect') })
    await user.click(first)

    await waitFor(() => expect(toGoogle).toHaveBeenCalledWith(expect.stringContaining('accounts.google.com')))
    const [call] = backend.callsTo(/\/business-profile\/links$/, 'POST')
    expect(call.body).toEqual({ locationId: 'loc-1' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a pending link picks its Google location with If-Match', async () => {
    const user = userEvent.setup()
    let current = binding({ status: 'pending', googleLocationTitle: null, googleLocationName: null, version: 1 })
    const backend = installFakeBackend(
      routes([], [
        {
          method: 'GET',
          match: /\/bindings\/b-1\/google-locations$/,
          handler: () => ({ items: [{ accountName: 'accounts/1', accountDisplayName: 'Garage Beispiel', locationName: 'locations/1', title: 'Garage Beispiel Zürich' }] }),
        },
        {
          method: 'PUT',
          match: /\/bindings\/b-1\/google-location$/,
          handler: () => {
            current = binding({ version: 2 })
            return current
          },
        },
      ]).map((r) => (r.match.source.includes('bindings$') ? { ...r, handler: () => ({ items: [current], configured: true }) } : r))
    )
    renderWithProviders(<BusinessProfileSection />)

    await user.click(await screen.findByRole('button', { name: i18n.t('businessProfile.card.actions.pick') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('textbox', { name: i18n.t('businessProfile.card.pick.label') }))
    await user.click(await screen.findByRole('option', { name: 'Garage Beispiel Zürich · Garage Beispiel' }))
    await user.click(within(dialog).getByRole('button', { name: i18n.t('businessProfile.card.pick.confirm') }))

    expect(await screen.findByText('Garage Beispiel Zürich')).toBeInTheDocument()
    const [put] = backend.callsTo(/google-location$/, 'PUT')
    expect(put.headers.get('If-Match')).toBe('1')
    expect(put.body).toEqual({ accountName: 'accounts/1', locationName: 'locations/1' })
  })

  it('disconnects after a confirmation, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend(
      routes([binding()], [{ method: 'DELETE', match: /\/bindings\/b-1$/, handler: () => status(204) }])
    )
    renderWithProviders(<BusinessProfileSection />)

    await user.click(await screen.findByRole('button', { name: i18n.t('businessProfile.card.actions.disconnect') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('businessProfile.card.disconnect.body'))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('businessProfile.card.disconnect.confirm') }))

    await waitFor(() => expect(backend.callsTo(/\/bindings\/b-1$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/\/bindings\/b-1$/, 'DELETE')[0].headers.get('If-Match')).toBe('3')
  })

  it('offers nothing until Nexotec has set up its client', async () => {
    installFakeBackend(routes([], [], false))
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText(i18n.t('businessProfile.card.notConfigured'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('businessProfile.card.actions.connect') })).not.toBeInTheDocument()
  })

  it('a reader sees the link and is offered no write', async () => {
    installFakeBackend([
      ...routes([binding()]),
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Test', lastName: 'Finance', email: 'f@example.ch', accessRoles: ['finance'], isDealerManager: false, status: 'active', version: 1 },
          activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' },
          memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' }],
          entitlements: ['sales'],
          writableCapabilities: [],
        }),
      },
    ])
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText('Garage Beispiel Zürich')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('businessProfile.card.actions.disconnect') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('businessProfile.card.actions.connect') })).not.toBeInTheDocument()
  })
})

describe('Google’s callback (KAN-226 PR-4)', () => {
  const page = (
    <Routes>
      <Route path="/business-profile-link/callback" element={<BusinessProfileLinkCallbackPage />} />
      <Route path="/administration/integrations" element={<p>integrations</p>} />
    </Routes>
  )

  it('posts the query once and returns to the integrations', async () => {
    const backend = installFakeBackend([
      { method: 'POST', match: /\/integrations\/business-profile\/link-callback$/, handler: () => ({ binding: binding({ status: 'pending' }), choices: [] }) },
    ])
    renderWithProviders(page, { route: '/business-profile-link/callback?state=abcdefghijklmnop&code=xyz', strictMode: true })

    expect(await screen.findByText('integrations')).toBeInTheDocument()
    const calls = backend.callsTo(/link-callback$/, 'POST')
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toEqual({ state: 'abcdefghijklmnop', code: 'xyz' })
  })

  it('says why a refused link failed, with the way back', async () => {
    installFakeBackend([
      {
        method: 'POST',
        match: /\/integrations\/business-profile\/link-callback$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'business_profile_consent_incomplete' } } }),
      },
    ])
    renderWithProviders(page, { route: '/business-profile-link/callback?state=abcdefghijklmnop&error=access_denied' })

    expect(await screen.findByText(i18n.t('businessProfile.refusals.business_profile_consent_incomplete'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('businessProfile.card.callback.back') })).toBeInTheDocument()
  })

  it('a callback without a state is refused without asking the server', async () => {
    const backend = installFakeBackend([])
    renderWithProviders(page, { route: '/business-profile-link/callback' })

    expect(await screen.findByText(i18n.t('businessProfile.card.callback.expired'))).toBeInTheDocument()
    expect(backend.callsTo(/link-callback$/)).toHaveLength(0)
  })

  // KAN-821: the card's «Fotos» line, from Nexotec's own upload records.
  it('counts the photos at Google, the refused and the waiting ones', async () => {
    installFakeBackend(routes([binding({ photos: { uploaded: 3, refused: 1, waiting: 2, stopped: false } })]))
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText('3 übertragen · 1 abgelehnt · 2 warten auf die Übertragung')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.fields.photos'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.photos.refusedHint'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('businessProfile.card.photos.stopped'))).not.toBeInTheDocument()
  })

  it('says when there are no images and when the upload stopped', async () => {
    installFakeBackend(
      routes([
        binding({ photos: { uploaded: 0, refused: 0, waiting: 0, stopped: false } }),
        binding({
          id: 'b-2', locationId: 'loc-2', locationLabel: 'Winterthur', googleLocationTitle: 'Garage Winterthur',
          photos: { uploaded: 1, refused: 0, waiting: 1, stopped: true },
        }),
      ])
    )
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText(i18n.t('businessProfile.card.photos.none'))).toBeInTheDocument()
    expect(screen.getByText('1 übertragen · 1 wartet auf die Übertragung')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('businessProfile.card.photos.stopped'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('businessProfile.card.photos.refusedHint'))).not.toBeInTheDocument()
  })

  it('shows no photo line for a binding without a summary', async () => {
    installFakeBackend(routes([binding({ status: 'pending', googleLocationTitle: null, photos: null })]))
    renderWithProviders(<BusinessProfileSection />)

    expect(await screen.findByText(i18n.t('businessProfile.card.pendingText'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('businessProfile.card.fields.photos'))).not.toBeInTheDocument()
  })

  it('counts in French with the plural', async () => {
    await i18n.loadLanguages('fr')
    await i18n.changeLanguage('fr')
    try {
      installFakeBackend(routes([binding({ photos: { uploaded: 1, refused: 2, waiting: 0, stopped: false } })]))
      renderWithProviders(<BusinessProfileSection />)
      expect(await screen.findByText('1 transférée · 2 refusées')).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
