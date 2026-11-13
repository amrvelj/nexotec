// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, type FakeRoute } from '../../test/fakeBackend'
import { CopyConfigurationSection } from './CopyConfigurationSection'

// KAN-220 — Business › «Konfiguration übernehmen von …»: only the active
// Betrieb's manager sees it, and only with a sister Betrieb to copy from; the
// preview is the server's dry run, the confirmation sends the same request
// with dryRun false, and the result says what was copied.

const ME = (isActiveDealershipManager: boolean): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch' },
    activeDealership: { id: 'dealership-1', legalName: 'Garage Ziel AG' },
    memberships: [
      { id: 'dealership-1', legalName: 'Garage Ziel AG' },
      { id: 'dealership-2', legalName: 'Garage Quelle AG' },
    ],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ALL_GATED_CAPABILITIES,
    isActiveDealershipManager,
  }),
})

const SOURCES: FakeRoute = {
  method: 'GET',
  match: /\/dealerships\/dealership-1\/copy-configuration\/sources$/,
  handler: () => ({ items: [{ id: 'dealership-2', legalName: 'Garage Quelle AG' }] }),
}

const result = (dryRun: boolean) => ({
  sourceDealershipId: 'dealership-2',
  sourceLegalName: 'Garage Quelle AG',
  dryRun,
  items: [
    { subset: 'standard_texts', key: 'offer.intro.standard', outcome: 'created', label: 'Unsere Einleitung', language: 'de', reason: null },
    { subset: 'document_template', key: 'header_notes', outcome: 'overwritten', label: null, language: null, reason: null },
    { subset: 'number_ranges', key: 'offer', outcome: 'skipped', label: 'OF-{counter:5}', language: null, reason: 'number_range_frozen' },
  ],
  counts: { created: 1, overwritten: 1, unchanged: 0, skipped: 1 },
})

describe('CopyConfigurationSection (KAN-220)', () => {
  it('previews the dry run, then copies with the same request and reports the result', async () => {
    const backend = installFakeBackend([
      ME(true),
      SOURCES,
      {
        method: 'POST',
        match: /\/dealerships\/dealership-1\/copy-configuration$/,
        handler: (req) => result((req.body as { dryRun: boolean }).dryRun),
      },
    ])
    const user = userEvent.setup()
    renderWithProviders(<CopyConfigurationSection dealershipId="dealership-1" />, { route: '/administration/business' })

    const section = within(await screen.findByRole('region', { name: 'Konfiguration übernehmen von …' }))
    const preview = section.getByRole('button', { name: 'Vorschau anzeigen' })
    expect(preview).toBeDisabled()
    await user.click(section.getByRole('textbox', { name: 'Betrieb, aus dem übernommen wird' }))
    await user.click(await screen.findByRole('option', { name: 'Garage Quelle AG' }))
    // Leave the number ranges out.
    await user.click(section.getByRole('checkbox', { name: (n) => n.startsWith('Nummernkreis-Muster') }))
    await user.click(preview)

    const table = within(await section.findByRole('table', { name: 'Was übernommen wird' }))
    expect(table.getByText('Unsere Einleitung')).toBeInTheDocument()
    expect(table.getByText('Neu')).toBeInTheDocument()
    expect(table.getByText('Wird überschrieben')).toBeInTheDocument()
    expect(table.getByText('Dieser Nummernkreis hat schon eine Nummer vergeben und ist gesperrt.')).toBeInTheDocument()
    const posts = () => backend.callsTo(/\/copy-configuration$/, 'POST')
    expect(posts()[0].body).toEqual({ sourceDealershipId: 'dealership-2', subsets: ['standard_texts', 'document_template'], dryRun: true })

    await user.click(section.getByRole('button', { name: '2 Änderungen übernehmen' }))
    await waitFor(() => expect(posts()).toHaveLength(2))
    expect(posts()[1].body).toEqual({ sourceDealershipId: 'dealership-2', subsets: ['standard_texts', 'document_template'], dryRun: false })
    expect(await section.findByText(/übernommen: 1 neu/)).toHaveTextContent(
      'Aus Garage Quelle AG übernommen: 1 neu, 1 überschrieben, 1 übersprungen.',
    )
  })

  it('is absent for a user who is not the active Betrieb’s manager', async () => {
    const backend = installFakeBackend([ME(false), SOURCES])
    renderWithProviders(
      <>
        <h1>Betrieb</h1>
        <CopyConfigurationSection dealershipId="dealership-1" />
      </>,
      { route: '/administration/business' },
    )
    expect(await screen.findByRole('heading', { name: 'Betrieb' })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/auth\/me$/, 'GET').length).toBeGreaterThan(0))
    expect(screen.queryByRole('region', { name: 'Konfiguration übernehmen von …' })).toBeNull()
    expect(backend.callsTo(/copy-configuration/, 'GET')).toHaveLength(0)
  })

  it('is absent when there is no sister Betrieb to copy from', async () => {
    const backend = installFakeBackend([
      ME(true),
      { method: 'GET', match: /\/copy-configuration\/sources$/, handler: () => ({ items: [] }) },
    ])
    renderWithProviders(<CopyConfigurationSection dealershipId="dealership-1" />, { route: '/administration/business' })
    await waitFor(() => expect(backend.callsTo(/\/copy-configuration\/sources$/, 'GET')).toHaveLength(1))
    expect(screen.queryByRole('region', { name: 'Konfiguration übernehmen von …' })).toBeNull()
  })
})
