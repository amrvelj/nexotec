// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { StandardTextsPage } from './StandardTextsPage'

// jsdom has no layout, so TanStack Virtual windows the library's grid down
// to zero rows; render every row instead (as BrandsAdmin's test does).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

// KAN-208 — Dealer Administration › Standard texts: the library by category
// (seeds labelled «Vorlage — bitte prüfen»), the editor with language tabs,
// only the placeholders the text's target offers, a preview through the
// server's renderer that says why a token cannot print, the adaptation of a
// seed, the "seed changed — review yours" flag and the per-category gate.

const row = (overrides: Record<string, unknown>) => ({
  id: 'seed-intro-de',
  dealershipId: null,
  locationId: null,
  scope: 'dealership',
  code: 'offer.intro.standard',
  category: 'document',
  appliesTo: 'offer',
  slot: 'intro',
  language: 'de',
  title: 'Einleitung der Offerte',
  body: 'Vielen Dank für Ihr Interesse.',
  source: 'platform_seed',
  overriddenFromId: null,
  seedChanged: false,
  isDefault: true,
  active: true,
  sortOrder: 10,
  templateApproved: false,
  providerTemplateId: null,
  version: 1,
  updatedAt: '2026-10-31T10:00:00Z',
  ...overrides,
})

const SEEDS = [
  row({}),
  row({ id: 'seed-intro-fr', language: 'fr', title: "Introduction de l'offre", body: 'Nous vous remercions.' }),
  row({ id: 'seed-intro-it', language: 'it', title: "Introduzione dell'offerta", body: 'La ringraziamo.' }),
  row({ id: 'seed-intro-en', language: 'en', title: 'Offer introduction', body: 'Thank you.' }),
  row({ id: 'seed-footer-de', code: 'offer.footer.validity', slot: 'footer', title: 'Gültigkeit der Offerte', body: 'Gültig ab {{document.date}}.' }),
  row({ id: 'seed-mail-de', code: 'email.offer_sent', category: 'email', slot: 'body', title: 'Offerte versandt', body: '{{customer.salutation}}' }),
]

const REGISTRY = {
  placeholders: ['dealership.name', 'location.phone', 'customer.salutation', 'vehicle.make_model', 'document.number', 'document.date', 'user.full_name', 'payment.due_date'],
  targets: [
    {
      category: 'document',
      appliesTo: 'offer',
      slots: ['header', 'intro', 'terms', 'closing', 'footer'],
      placeholders: ['dealership.name', 'location.phone', 'vehicle.make_model', 'document.number', 'document.date', 'user.full_name'],
    },
    { category: 'email', appliesTo: 'offer', slots: ['body'], placeholders: ['dealership.name', 'customer.salutation'] },
  ],
}

const ALL = ['document', 'email', 'sms', 'whatsapp', 'internal_note', 'workshop']

function routes({ items = SEEDS, writable = ALL, extra = [] }: { items?: unknown[]; writable?: string[]; extra?: FakeRoute[] } = {}): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1\/standard-texts$/, handler: () => ({ items, writableCategories: writable }) },
    { method: 'GET', match: /\/standard-text-registry$/, handler: () => REGISTRY },
    {
      method: 'POST',
      match: /\/dealerships\/dealership-1\/standard-text-previews$/,
      handler: (req) => ({ text: String((req.body as { body: string }).body).replace('{{document.date}}', '31.10.2026') }),
    },
  ]
}

/** KAN-516 — a text opens from its row menu (ADR-061): «Bearbeiten», or
 * «Ansehen» in a category the user may not write. */
async function openFromRowMenu(user: ReturnType<typeof userEvent.setup>, title: string, item: 'Bearbeiten' | 'Ansehen' = 'Bearbeiten') {
  const table = await screen.findByRole('table', { name: 'Dokumente' })
  const line = within(table).getByText(title).closest('[role="row"]') as HTMLElement
  await user.click(within(line).getByRole('button', { name: 'Zeilenaktionen' }))
  await user.click(await screen.findByRole('menuitem', { name: item }))
  return within(await screen.findByRole('dialog'))
}

const openEditor = (user: ReturnType<typeof userEvent.setup>, title: string) => openFromRowMenu(user, title)

/** The titles down the grid, in the order shown. */
function titlesShown(table: HTMLElement): string[] {
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((line) => within(line).getAllByRole('cell')[0].textContent ?? '')
}

describe('StandardTextsPage (KAN-208)', () => {
  it('lists the library by category with the templates labelled for review', async () => {
    installFakeBackend(routes())
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    expect(within(table).getByText('Einleitung der Offerte')).toBeInTheDocument()
    expect(within(table).getByText('Gültigkeit der Offerte')).toBeInTheDocument()
    expect(within(table).getAllByText('Vorlage — bitte prüfen')).toHaveLength(2)
    // One entry per code, its four languages beside it — not one line per language.
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(screen.getByRole('tab', { name: 'Dokumente (2)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'E-Mail (1)' })).toBeInTheDocument()
    expect(screen.getByText(/keine Rechtsberatung/)).toBeInTheDocument()
  })

  it('offers only the target’s placeholders, previews on sample data and adapts a seed per language', async () => {
    const backend = installFakeBackend(
      routes({
        extra: [
          {
            method: 'POST',
            match: /\/standard-texts\/document$/,
            handler: (req) => row({ ...(req.body as object), id: 'own-1', dealershipId: 'dealership-1', source: 'dealer', overriddenFromId: 'seed-intro-fr' }),
          },
        ],
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })
    const dialog = await openEditor(user, 'Einleitung der Offerte')

    expect(dialog.getByRole('tab', { name: 'Deutsch' })).toBeInTheDocument()
    expect(dialog.getByRole('tab', { name: 'Italienisch' })).toBeInTheDocument()
    const placeholders = within(dialog.getByRole('group', { name: 'Platzhalter einfügen' }))
    expect(placeholders.getByRole('button', { name: 'Dokumentdatum' })).toBeInTheDocument()
    // The offer document has no payment due date and no salutation.
    expect(placeholders.queryByRole('button', { name: 'Zahlungsfrist' })).toBeNull()
    expect(placeholders.queryByRole('button', { name: 'Anrede' })).toBeNull()
    expect(dialog.getByText('Vorlage — bitte prüfen')).toBeInTheDocument()

    // The French tab, a placeholder inserted, the preview from the server's renderer.
    await user.click(dialog.getByRole('tab', { name: 'Französisch' }))
    const body = dialog.getByRole('textbox', { name: 'Text' })
    expect(body).toHaveValue('Nous vous remercions.')
    await user.click(body)
    await user.keyboard('{End} ')
    await user.click(placeholders.getByRole('button', { name: 'Dokumentdatum' }))
    expect(body).toHaveValue('Nous vous remercions. {{document.date}}')
    expect(await dialog.findByText('Nous vous remercions. 31.10.2026', {}, { timeout: 3000 })).toBeInTheDocument()

    await user.click(dialog.getByRole('button', { name: 'Als eigenen Text übernehmen' }))
    await waitFor(() => expect(backend.callsTo(/\/standard-texts\/document$/, 'POST')).toHaveLength(1))
    const [post] = backend.callsTo(/\/standard-texts\/document$/, 'POST')
    expect(post.body).toMatchObject({ code: 'offer.intro.standard', language: 'fr', body: 'Nous vous remercions. {{document.date}}' })
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    const previews = backend.callsTo(/\/standard-text-previews$/, 'POST')
    expect(previews.at(-1)?.body).toMatchObject({ category: 'document', appliesTo: 'offer', language: 'fr' })
  })

  it('says why a token cannot print, never the raw token', async () => {
    installFakeBackend(
      routes({
        extra: [
          {
            method: 'POST',
            match: /\/standard-text-previews$/,
            handler: () =>
              status(422, {
                error: {
                  code: 'unprocessable_entity',
                  message: 'x',
                  details: { reason: 'standard_text_placeholder_unresolved', placeholders: [{ token: 'payment.due_date', problem: 'not_valid_here' }] },
                },
              }),
          },
        ],
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })
    const dialog = await openEditor(user, 'Gültigkeit der Offerte')

    const preview = dialog.getByRole('region', { name: 'Vorschau' })
    expect(await within(preview).findByText('Hier nicht verfügbar: {{payment.due_date}}', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(within(preview).getByText('Dieser Text kann so nicht gedruckt werden:')).toBeInTheDocument()
  })

  it('flags an adaptation whose template changed and records the review with If-Match', async () => {
    const own = row({ id: 'own-de', dealershipId: 'dealership-1', source: 'dealer', overriddenFromId: 'seed-intro-de', seedChanged: true, body: 'Grüezi.', version: 3 })
    const backend = installFakeBackend(
      routes({
        items: [...SEEDS, own],
        extra: [{ method: 'PATCH', match: /\/standard-texts\/document\/own-de$/, handler: () => ({ ...own, seedChanged: false, version: 4 }) }],
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    expect(within(table).getByText('Angepasst')).toBeInTheDocument()
    expect(within(table).getByText('Vorlage geändert — bitte prüfen')).toBeInTheDocument()
    const dialog = await openEditor(user, 'Einleitung der Offerte')
    expect(dialog.getByText('Die Vorlage wurde geändert — bitte prüfen Sie Ihren Text')).toBeInTheDocument()
    expect(dialog.getByLabelText('Aktuelle Vorlage')).toHaveTextContent('Vielen Dank für Ihr Interesse.')
    expect(dialog.getByRole('textbox', { name: 'Text' })).toHaveValue('Grüezi.')

    await user.click(dialog.getByRole('button', { name: 'Als geprüft markieren' }))
    await waitFor(() => expect(backend.callsTo(/\/own-de$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/own-de$/, 'PATCH')
    expect(patch.body).toEqual({ seedReviewed: true })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('offers writes only in the categories the user may write (FR-A-28)', async () => {
    installFakeBackend(routes({ writable: ['email'] }))
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    expect(screen.getByText('Sie können die Texte dieser Kategorie lesen, aber nicht ändern.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Neuer Text' })).toBeNull()
    expect(within(table).getByText('Einleitung der Offerte')).toBeInTheDocument()
    const dialog = await openFromRowMenu(user, 'Einleitung der Offerte', 'Ansehen')
    expect(dialog.queryByRole('button', { name: 'Als eigenen Text übernehmen' })).toBeNull()
    expect(dialog.queryByRole('group', { name: 'Platzhalter einfügen' })).toBeNull()
    expect(dialog.getByRole('textbox', { name: 'Text' })).toHaveAttribute('readonly')
    await user.click(dialog.getByRole('button', { name: 'Abbrechen' }))

    await user.click(screen.getByRole('tab', { name: 'E-Mail (1)' }))
    expect(await screen.findByRole('button', { name: 'Neuer Text' })).toBeInTheDocument()
  })

  it('sorts the library in the browser, the sort in the URL, without asking the API again (KAN-516)', async () => {
    const backend = installFakeBackend(routes())
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts?category=document' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    // No sort: the library's own order (use, then position: footer before intro).
    expect(titlesShown(table)).toEqual(['Gültigkeit der Offerte', 'Einleitung der Offerte'])
    const reads = backend.callsTo(/\/standard-texts$/, 'GET').length

    const heading = within(table).getByRole('columnheader', { name: 'Bezeichnung' })
    await user.click(heading)
    expect(heading).toHaveAttribute('aria-sort', 'ascending')
    expect(titlesShown(table)).toEqual(['Einleitung der Offerte', 'Gültigkeit der Offerte'])
    await user.click(heading)
    expect(heading).toHaveAttribute('aria-sort', 'descending')
    expect(titlesShown(table)).toEqual(['Gültigkeit der Offerte', 'Einleitung der Offerte'])
    expect(backend.callsTo(/\/standard-texts$/, 'GET')).toHaveLength(reads)
  })

  it('reads a pasted sort from the URL and keeps it across the category tabs', async () => {
    installFakeBackend(routes())
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts?sort=title:asc' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    expect(titlesShown(table)).toEqual(['Einleitung der Offerte', 'Gültigkeit der Offerte'])
    await user.click(screen.getByRole('tab', { name: 'E-Mail (1)' }))
    expect(within(await screen.findByRole('table', { name: 'E-Mail' })).getByRole('columnheader', { name: 'Bezeichnung' })).toHaveAttribute(
      'aria-sort',
      'ascending',
    )
  })

  it('offers its stored fields as columns in the Columns panel, the code and last change hidden by default (ADR-060)', async () => {
    installFakeBackend(routes())
    const user = userEvent.setup()
    renderWithProviders(<StandardTextsPage />, { route: '/administration/texts' })

    const table = await screen.findByRole('table', { name: 'Dokumente' })
    const headings = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headings).toEqual(expect.arrayContaining(['Bezeichnung', 'Verwendet für', 'Position', 'Herkunft', 'Sprachen', 'Standard']))
    expect(headings).not.toContain('Code')
    expect(within(table).queryByText('offer.intro.standard')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Spalten' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Code' }))
    expect(await within(table).findByText('offer.intro.standard')).toBeInTheDocument()
  })
})
