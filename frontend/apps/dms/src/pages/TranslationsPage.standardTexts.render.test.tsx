// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { TranslationsPage } from './TranslationsPage'

// KAN-209 on KAN-208's library — the standard-text seeds tab of Platform
// Administration › Translations: one row per code, title and body each in the
// four-column editor with the diff against the shipped wording, publish
// (If-Match on the seed row) and revert; read-only for anyone but platform staff.

type Lang = 'de' | 'fr' | 'it' | 'en'
const SHIPPED: Record<Lang, [string, string]> = {
  de: ['Gültigkeit der Offerte', 'Diese Offerte ist 30 Tage ab dem {{document.date}} gültig.'],
  fr: ["Validité de l'offre", 'Cette offre est valable 30 jours à compter du {{document.date}}.'],
  it: ["Validità dell'offerta", 'La presente offerta è valida 30 giorni dal {{document.date}}.'],
  en: ['Offer validity', 'This offer is valid for 30 days from {{document.date}}.'],
}

function seedRow(language: Lang, overrides: Partial<{ title: string; body: string; version: number }> = {}) {
  const [shippedTitle, shippedBody] = SHIPPED[language]
  const title = overrides.title ?? shippedTitle
  const body = overrides.body ?? shippedBody
  return {
    id: `seed-${language}`,
    language,
    title,
    body,
    shippedTitle,
    shippedBody,
    edited: title !== shippedTitle || body !== shippedBody,
    active: true,
    version: overrides.version ?? 1,
    updatedAt: '2026-10-31T10:00:00Z',
    updatedBy: null,
  }
}

function group(rows: Partial<Record<Lang, ReturnType<typeof seedRow>>> = {}) {
  return {
    code: 'offer.footer.validity',
    category: 'document',
    appliesTo: 'offer',
    slot: 'footer',
    de: seedRow('de'),
    fr: seedRow('fr'),
    it: seedRow('it'),
    en: seedRow('en'),
    ...rows,
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
  return <span data-testid="location">{useLocation().search}</span>
}

function install(routes: FakeRoute[] = [], writable = ALL_GATED_CAPABILITIES, listed = () => [group()]) {
  return installFakeBackend([
    session(writable),
    ...routes,
    { method: 'GET', match: /^\/platform\/standard-text-seeds$/, handler: () => ({ items: listed() }) },
  ])
}

function renderPage(route = '/platform/translations?source=standardTexts') {
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

describe('TranslationsPage › standard-text seeds (KAN-209)', () => {
  it('lists the seeds by code with their use and four languages, the tab in the URL', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderPage('/platform/translations')
    await user.click(await screen.findByRole('tab', { name: 'Standardtext-Vorlagen' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('source=standardTexts'))
    expect(await screen.findByText('offer.footer.validity')).toBeInTheDocument()
    expect(screen.getByText('Dokumente · Offerte · Fusszeile')).toBeInTheDocument()
    expect(screen.getByText("Validité de l'offre")).toBeInTheDocument()
    expect(screen.getByText('1 Vorlage')).toBeInTheDocument()

    await user.type(screen.getByRole('textbox', { name: 'Suchen' }), 'validity')
    await waitFor(() =>
      expect(backend.callsTo(/^\/platform\/standard-text-seeds$/).some((c) => c.params.get('q') === 'validity')).toBe(true),
    )
  })

  it('publishes a body with If-Match on the seed row, showing the diff against the shipped wording', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'PUT',
        match: /^\/platform\/standard-text-seeds\/seed-fr$/,
        handler: (req) => seedRow('fr', { body: (req.body as { body: string }).body, version: 2 }),
      },
    ])
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Bearbeiten' }))
    const bodyEditor = await screen.findByRole('region', { name: 'Text: offer.footer.validity' })
    expect(screen.getByRole('region', { name: 'Titel: offer.footer.validity' })).toBeInTheDocument()

    const fr = within(bodyEditor).getByRole('textbox', { name: 'Französisch' })
    await user.clear(fr)
    await user.type(fr, 'Cette offre est valable 20 jours à compter du {{{{document.date}}.')
    expect(within(bodyEditor).getByText('Abweichung vom ausgelieferten Text')).toBeInTheDocument()

    await user.click(within(bodyEditor).getByRole('button', { name: 'Veröffentlichen' }))
    expect(await within(bodyEditor).findByRole('alert')).toHaveTextContent('Veröffentlicht')
    const puts = backend.callsTo(/^\/platform\/standard-text-seeds\//, 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0].body).toEqual({ body: 'Cette offre est valable 20 jours à compter du {{document.date}}.' })
    expect(puts[0].headers.get('If-Match')).toBe('1')
    expect(within(bodyEditor).getAllByText('Bearbeitet')).toHaveLength(1)
  })

  it('says a refused placeholder in the UI language', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'PUT',
        match: /^\/platform\/standard-text-seeds\/seed-de$/,
        handler: () =>
          status(422, {
            error: {
              code: 'unprocessable_entity',
              message: 'x',
              details: { reason: 'standard_text_placeholder_invalid', placeholders: [{ token: 'nonsense', problem: 'unknown' }] },
            },
          }),
      },
    ])
    renderPage('/platform/translations?source=standardTexts&code=offer.footer.validity')
    const bodyEditor = await screen.findByRole('region', { name: 'Text: offer.footer.validity' })
    await user.type(within(bodyEditor).getByRole('textbox', { name: 'Deutsch' }), ' {{{{nonsense}}')
    await user.click(within(bodyEditor).getByRole('button', { name: 'Veröffentlichen' }))
    expect(
      await within(bodyEditor).findByText((text) => text.startsWith('Der Text verwendet Platzhalter, die hier nicht erlaubt sind') && text.includes('nonsense')),
    ).toBeInTheDocument()
    expect(within(bodyEditor).getByRole('textbox', { name: 'Deutsch' })).toHaveAttribute('aria-invalid', 'true')
  })

  it('reverts an edited language to the shipped wording', async () => {
    const user = userEvent.setup()
    const backend = install(
      [{ method: 'POST', match: /^\/platform\/standard-text-seeds\/seed-it\/revert$/, handler: () => seedRow('it', { version: 4 }) }],
      ALL_GATED_CAPABILITIES,
      () => [group({ it: seedRow('it', { body: 'Valida 10 giorni.', version: 3 }) })],
    )
    renderPage('/platform/translations?source=standardTexts&code=offer.footer.validity')
    const bodyEditor = await screen.findByRole('region', { name: 'Text: offer.footer.validity' })
    await user.click(within(bodyEditor).getByRole('button', { name: 'Ausgelieferten Text wiederherstellen' }))
    await waitFor(() => expect(backend.callsTo(/\/revert$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/revert$/, 'POST')[0].headers.get('If-Match')).toBe('3')
    await waitFor(() => expect(within(bodyEditor).getByRole('textbox', { name: 'Italienisch' })).toHaveValue(SHIPPED.it[1]))
  })

  it('shows anyone else the seeds read-only', async () => {
    const user = userEvent.setup()
    install([], ['configurations'])
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Ansehen' }))
    expect(await screen.findAllByText('Nur Plattformadministratoren von Nexotec können diese Texte ändern.')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Veröffentlichen' })).not.toBeInTheDocument()
  })
})
