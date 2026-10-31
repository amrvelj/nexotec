// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { TranslationsPage } from './TranslationsPage'

// KAN-209 — Platform Administration › Translations: search, the four-column
// editor, the diff against the shipped text, publish and revert; the writes
// are platform staff's (KAN-430: anyone else reads, the actions are hidden).

type Lang = 'de' | 'fr' | 'it' | 'en'

function row(language: Lang, value: string, seedValue = value, version = 1) {
  return {
    id: `row-${language}`,
    namespace: 'shell',
    key: 'nav.business',
    language,
    value,
    seedValue,
    source: value === seedValue ? 'bundle_seed' : 'platform',
    version,
    updatedAt: '2026-10-31T10:00:00Z',
    updatedBy: null,
  }
}

function businessKey(overrides: Partial<Record<Lang, ReturnType<typeof row>>> = {}) {
  return {
    namespace: 'shell',
    key: 'nav.business',
    de: row('de', 'Betrieb'),
    fr: row('fr', 'Entreprise'),
    it: row('it', 'Azienda'),
    en: row('en', 'Business'),
    ...overrides,
  }
}

function session(writableCapabilities: string[]): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Pia', lastName: 'Plattform', email: 'pia@nexotec.ch', accessRoles: ['platform_admin'], isDealerManager: false },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ALL_ENTITLEMENTS,
      writableCapabilities,
    }),
  }
}

function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.search}</span>
}

function install(routes: FakeRoute[] = [], writable = ALL_GATED_CAPABILITIES) {
  return installFakeBackend([
    session(writable),
    ...routes,
    {
      method: 'GET',
      match: /^\/platform\/ui-translations$/,
      handler: (req) => {
        const q = req.params.get('q') ?? ''
        const items = [businessKey()].filter(() => !q || 'shell.nav.business'.includes(q))
        return { items, nextCursor: null, total: items.length, totalIsEstimate: false }
      },
    },
    { method: 'GET', match: /^\/platform\/ui-translations\/namespaces$/, handler: () => ({ items: ['common', 'shell'] }) },
    {
      method: 'GET',
      match: /^\/platform\/ui-translations\/missing$/,
      handler: () => ({
        items: [{ key: 'kan209.gone', language: 'fr', reportCount: 1234, firstReportedAt: '2026-10-30T08:00:00Z', lastReportedAt: '2026-10-31T09:00:00Z' }],
      }),
    },
  ])
}

function renderPage(route = '/platform/translations') {
  return renderWithProviders(
    <Routes>
      <Route
        path="/platform/translations"
        element={
          <>
            <TranslationsPage />
            <LocationProbe />
          </>
        }
      />
    </Routes>,
    { route },
  )
}

afterEach(cleanup)

describe('TranslationsPage (KAN-209)', () => {
  it('lists keys with their four languages, one tab per source, and the missing-key log', async () => {
    install()
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Übersetzungen' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'UI-Texte' })).toHaveAttribute('aria-selected', 'true')
    const table = (await screen.findAllByRole('table'))[0]
    expect(within(table).getByText('shell.nav.business')).toBeInTheDocument()
    expect(within(table).getByText('Entreprise')).toBeInTheDocument()
    expect(screen.getByText('1 Schlüssel')).toBeInTheDocument()
    expect(await screen.findByText('kan209.gone')).toBeInTheDocument()
    expect(screen.getByText("1'234")).toBeInTheDocument()
  })

  it('keeps the search in the URL and sends it to the API', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderPage()
    await screen.findByText('shell.nav.business')
    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'nav.bus')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('q=nav.bus'))
    await waitFor(() =>
      expect(backend.callsTo(/^\/platform\/ui-translations$/).some((c) => c.params.get('q') === 'nav.bus')).toBe(true),
    )
  })

  it('publishes a changed language with If-Match, shows the diff against the seed, and only that language', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'PUT',
        match: /^\/platform\/ui-translations\/shell\/nav\.business\/fr$/,
        handler: (req) => row('fr', (req.body as { value: string }).value, 'Entreprise', 2),
      },
    ])
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Bearbeiten' }))
    const editor = await screen.findByRole('region', { name: 'shell.nav.business' })
    const fr = within(editor).getByRole('textbox', { name: 'Französisch' })
    await user.clear(fr)
    await user.type(fr, 'Société')
    expect(within(editor).getByText('Abweichung vom ausgelieferten Text')).toBeInTheDocument()
    expect(within(editor).getByText('Entreprise', { selector: 'del' })).toBeInTheDocument()
    expect(within(editor).getByText('Société', { selector: 'ins' })).toBeInTheDocument()

    await user.click(within(editor).getByRole('button', { name: 'Veröffentlichen' }))
    expect(await within(editor).findByRole('alert')).toHaveTextContent('Veröffentlicht')
    const puts = backend.callsTo(/^\/platform\/ui-translations\//, 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0].body).toEqual({ value: 'Société' })
    expect(puts[0].headers.get('If-Match')).toBe('1')
    // The published row is shown as the platform's.
    expect(within(editor).getByText('Bearbeitet')).toBeInTheDocument()
  })

  it('says a refused publish in the UI language beside its language, and keeps that draft while the others publish', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'PUT',
        match: /^\/platform\/ui-translations\/shell\/nav\.business\/de$/,
        handler: () =>
          status(422, { error: { code: 'unprocessable_entity', message: 'x', details: { reason: 'ui_translation_placeholders_changed' } } }),
      },
      {
        method: 'PUT',
        match: /^\/platform\/ui-translations\/shell\/nav\.business\/en$/,
        handler: (req) => row('en', (req.body as { value: string }).value, 'Business', 2),
      },
    ])
    renderPage('/platform/translations?key=shell.nav.business')
    const de = await screen.findByRole('textbox', { name: 'Deutsch' })
    await user.type(de, ' AG')
    await user.type(screen.getByRole('textbox', { name: 'Englisch' }), ' unit')
    await user.click(screen.getByRole('button', { name: 'Veröffentlichen' }))
    expect(await screen.findByText('Der Text muss die Platzhalter des ausgelieferten Textes behalten.')).toBeInTheDocument()
    // The refused language keeps what was typed; the published one shows its new live text.
    expect(screen.getByRole('textbox', { name: 'Deutsch' })).toHaveValue('Betrieb AG')
    expect(screen.getByRole('textbox', { name: 'Englisch' })).toHaveValue('Business unit')
    const editor = screen.getByRole('region', { name: 'shell.nav.business' })
    expect(within(editor).getAllByText('Bearbeitet')).toHaveLength(1)
  })

  it('reverts an edited language to the shipped text', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'GET',
        match: /^\/platform\/ui-translations$/,
        handler: () => ({ items: [businessKey({ it: row('it', 'Ditta', 'Azienda', 3) })], nextCursor: null, total: 1, totalIsEstimate: false }),
      },
      {
        method: 'POST',
        match: /^\/platform\/ui-translations\/shell\/nav\.business\/it\/revert$/,
        handler: () => row('it', 'Azienda', 'Azienda', 4),
      },
    ])
    renderPage('/platform/translations?key=shell.nav.business')
    await user.click(await screen.findByRole('button', { name: 'Ausgelieferten Text wiederherstellen' }))
    await waitFor(() => expect(backend.callsTo(/\/revert$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/revert$/, 'POST')[0].headers.get('If-Match')).toBe('3')
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Italienisch' })).toHaveValue('Azienda'))
  })

  it('shows anyone else the strings read-only: no publish, no revert, «Ansehen»', async () => {
    const user = userEvent.setup()
    install([], ['configurations', 'stock_items'])
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Ansehen' }))
    expect(await screen.findByText('Nur Plattformadministratoren von Nexotec können UI-Texte ändern.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Veröffentlichen' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Deutsch' })).toHaveAttribute('readonly')
  })
})
