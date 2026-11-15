// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { TranslationsPage } from './TranslationsPage'

// KAN-227 — the AI prompt seeds tab of Platform Administration › Translations:
// per code its title, instructions and request in the four-column editor with
// the diff against the shipped wording; publish with If-Match on the seed
// row; read-only for anyone but platform staff.

type Lang = 'de' | 'fr' | 'it' | 'en'

function seedRow(language: Lang, overrides: Partial<{ systemPrompt: string; version: number }> = {}) {
  const shippedSystemPrompt = `Vorgabe ${language}.`
  const systemPrompt = overrides.systemPrompt ?? shippedSystemPrompt
  return {
    id: `seed-${language}`,
    language,
    title: `Titel ${language}`,
    systemPrompt,
    userPrompt: 'Fahrzeug: {{ vehicle.label }}',
    shippedTitle: `Titel ${language}`,
    shippedSystemPrompt,
    shippedUserPrompt: 'Fahrzeug: {{ vehicle.label }}',
    edited: systemPrompt !== shippedSystemPrompt,
    version: overrides.version ?? 1,
    updatedAt: '2026-11-15T10:00:00Z',
    updatedBy: null,
  }
}

const group = () => ({
  code: 'vehicle_listing',
  placeholders: ['vehicle.label'],
  de: seedRow('de'),
  fr: seedRow('fr'),
  it: seedRow('it'),
  en: seedRow('en'),
})

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

function install(routes: FakeRoute[] = [], writable = ALL_GATED_CAPABILITIES) {
  return installFakeBackend([
    session(writable),
    ...routes,
    { method: 'GET', match: /^\/platform\/ai-prompt-seeds$/, handler: () => ({ items: [group()] }) },
  ])
}

const renderPage = () =>
  renderWithProviders(
    <Routes>
      <Route path="/platform/translations" element={<TranslationsPage />} />
    </Routes>,
    { route: '/platform/translations?source=aiPrompts' },
  )

afterEach(cleanup)

describe('TranslationsPage › AI prompts (KAN-227)', () => {
  it('is a tab of its own and publishes the instructions with If-Match on the seed row', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'PUT',
        match: /^\/platform\/ai-prompt-seeds\/seed-fr$/,
        handler: (req) => seedRow('fr', { systemPrompt: (req.body as { systemPrompt: string }).systemPrompt, version: 2 }),
      },
    ])
    renderPage()
    expect(await screen.findByRole('tab', { name: i18n.t('translations.sources.aiPrompts') })).toHaveAttribute('aria-selected', 'true')
    const editor = await screen.findByRole('region', { name: `${i18n.t('aiPrompts.fields.systemPrompt')}: vehicle_listing` })

    const fr = within(editor).getByRole('textbox', { name: i18n.t('translations.languages.fr') })
    await user.clear(fr)
    await user.type(fr, 'Nouvelle consigne.')
    await user.click(within(editor).getByRole('button', { name: i18n.t('translations.editor.publish') }))

    expect(await within(editor).findByRole('alert')).toBeInTheDocument()
    const puts = backend.callsTo(/^\/platform\/ai-prompt-seeds\//, 'PUT')
    expect(puts).toHaveLength(1)
    expect(puts[0].body).toEqual({ systemPrompt: 'Nouvelle consigne.' })
    expect(puts[0].headers.get('If-Match')).toBe('1')
  })

  it('is read-only for anyone but platform staff', async () => {
    install([], [])
    renderPage()
    const editor = await screen.findByRole('region', { name: `${i18n.t('aiPrompts.fields.systemPrompt')}: vehicle_listing` })
    expect(within(editor).queryByRole('button', { name: i18n.t('translations.editor.publish') })).not.toBeInTheDocument()
  })
})
