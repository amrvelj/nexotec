// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { TranslationsPage } from './TranslationsPage'

// jsdom has no layout, so TanStack Virtual windows the KAN-516 grids down to
// zero rows; render every row instead (as BrandsAdmin's test does) — or, for
// a test that sets `virtualWindow.rows`, only that many, as a short screen
// would, so the grid does not ask for the next page by itself.
const virtualWindow = vi.hoisted(() => ({ rows: Number.POSITIVE_INFINITY }))
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () =>
        Array.from({ length: Math.min(count, virtualWindow.rows) }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

/** KAN-516 — a row opens from its row menu (ADR-061). */
async function openFromRowMenu(user: ReturnType<typeof userEvent.setup>, item: 'Bearbeiten' | 'Ansehen') {
  await user.click((await screen.findAllByRole('button', { name: 'Zeilenaktionen' }))[0])
  await user.click(await screen.findByRole('menuitem', { name: item }))
}

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

afterEach(() => {
  cleanup()
  virtualWindow.rows = Number.POSITIVE_INFINITY
})

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
    // KAN-516: both lists are the kit's grid, each with its Columns panel;
    // the area and the last change are columns, hidden by default (ADR-060).
    expect(screen.getAllByRole('button', { name: 'Spalten' })).toHaveLength(2)
    const keys = screen.getByRole('table', { name: 'UI-Texte' })
    expect(within(keys).queryByRole('columnheader', { name: 'Bereich' })).toBeNull()
    expect(within(screen.getByRole('table', { name: 'Fehlende Schlüssel' })).queryByRole('columnheader', { name: 'Zuerst gemeldet' })).toBeNull()
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
    await openFromRowMenu(user, 'Bearbeiten')
    const editor = await screen.findByRole('region', { name: 'Schlüssel: shell.nav.business' })
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
    const editor = screen.getByRole('region', { name: 'Schlüssel: shell.nav.business' })
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
    await openFromRowMenu(user, 'Ansehen')
    expect(await screen.findByText('Nur Plattformadministratoren von Nexotec können diese Texte ändern.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Veröffentlichen' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Deutsch' })).toHaveAttribute('readonly')
  })

  it('sorts the keys in the browser over the whole result: a sort loads the pages not yet loaded (KAN-516)', async () => {
    const user = userEvent.setup()
    // A first page of twelve keys and a screen showing one row: the grid
    // itself would not ask for the second page yet.
    virtualWindow.rows = 1
    const key = (index: number) => {
      const name = `filler${String(index).padStart(2, '0')}`
      const value = (language: Lang) => ({ ...row(language, `Text ${index}`), namespace: 'common', key: name })
      return { namespace: 'common', key: name, de: value('de'), fr: value('fr'), it: value('it'), en: value('en') }
    }
    const backend = install([
      {
        method: 'GET',
        match: /^\/platform\/ui-translations$/,
        handler: (req) =>
          req.params.get('cursor') === 'page-2'
            ? { items: [businessKey()], nextCursor: null, total: 13, totalIsEstimate: false }
            : { items: Array.from({ length: 12 }, (_, index) => key(index)), nextCursor: 'page-2', total: 13, totalIsEstimate: false },
      },
    ])
    renderPage()
    const secondPageAsked = () => backend.callsTo(/^\/platform\/ui-translations$/).some((c) => c.params.get('cursor') === 'page-2')

    const table = await screen.findByRole('table', { name: 'UI-Texte' })
    expect(within(table).getByText('common.filler00')).toBeInTheDocument()
    expect(secondPageAsked()).toBe(false)

    // German, ascending: «Betrieb» (second page) before «Text 0» — so the
    // second page is loaded before the rows are ordered.
    const german = within(table).getByRole('columnheader', { name: 'Deutsch' })
    await user.click(german)
    expect(german).toHaveAttribute('aria-sort', 'ascending')
    await waitFor(() => expect(secondPageAsked()).toBe(true))
    expect(await within(table).findByText('shell.nav.business')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('sort=de%3Aasc'))
  })

  it('sorts the missing-key log in the browser by its own URL sort, beside the keys', async () => {
    install([
      {
        method: 'GET',
        match: /^\/platform\/ui-translations\/missing$/,
        handler: () => ({
          items: [
            { key: 'kan209.gone', language: 'fr', reportCount: 3, firstReportedAt: '2026-10-30T08:00:00Z', lastReportedAt: '2026-10-31T09:00:00Z' },
            { key: 'kan209.often', language: 'de', reportCount: 1234, firstReportedAt: '2026-10-29T08:00:00Z', lastReportedAt: '2026-10-30T09:00:00Z' },
          ],
        }),
      },
    ])
    renderPage('/platform/translations?missingSort=count:desc')

    const log = await screen.findByRole('table', { name: 'Fehlende Schlüssel' })
    await within(log).findByText('kan209.often')
    const keys = within(log)
      .getAllByRole('row')
      .slice(1)
      .map((line) => within(line).getAllByRole('cell')[0].textContent)
    expect(keys).toEqual(['kan209.often', 'kan209.gone'])
    expect(within(log).getByRole('columnheader', { name: 'Meldungen' })).toHaveAttribute('aria-sort', 'descending')
    // The keys' own grid is not sorted by the log's parameter.
    expect(within(screen.getByRole('table', { name: 'UI-Texte' })).getByRole('columnheader', { name: 'Deutsch' })).toHaveAttribute('aria-sort', 'none')
  })

  // KAN-709: the log is read in one page of the latest reports (no cursor), so
  // a browser sort orders only that page — the card says so when it is full.
  function missingReports(count: number) {
    return Array.from({ length: count }, (_, index) => ({
      key: `kan709.key${String(index).padStart(3, '0')}`,
      language: 'de',
      reportCount: index + 1,
      firstReportedAt: '2026-11-01T08:00:00Z',
      lastReportedAt: '2026-11-02T08:00:00Z',
    }))
  }

  it('asks for the latest 100 reports and says a sort covers only them when the page is full', async () => {
    const backend = install([
      { method: 'GET', match: /^\/platform\/ui-translations\/missing$/, handler: () => ({ items: missingReports(100) }) },
    ])
    renderPage('/platform/translations?missingSort=count:desc')

    expect(
      await screen.findByText('Gezeigt werden nur die 100 zuletzt gemeldeten Schlüssel; eine Sortierung ordnet nur diese.'),
    ).toBeInTheDocument()
    const asked = backend.calls.filter((call) => call.pathname === '/platform/ui-translations/missing')
    expect(asked.length).toBeGreaterThan(0)
    expect(asked.every((call) => call.params.get('limit') === '100')).toBe(true)
  })

  it('says nothing more when the log fits in the page', async () => {
    install([
      { method: 'GET', match: /^\/platform\/ui-translations\/missing$/, handler: () => ({ items: missingReports(99) }) },
    ])
    renderPage('/platform/translations')

    const log = await screen.findByRole('table', { name: 'Fehlende Schlüssel' })
    await within(log).findByText('kan709.key000')
    expect(screen.queryByTestId('missing-latest-only')).not.toBeInTheDocument()
  })
})
