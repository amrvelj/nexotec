// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { AiAdministrationPage } from './AiAdministrationPage'

// KAN-227 — Dealer Administration › KI-Texte: the AI connection in use and the
// Betrieb's wording of each prompt — Nexotec's default until adapted, «the
// default has changed» when Nexotec's moves on, and back to the default.

const PLACEHOLDERS = ['vehicle.label', 'vehicle.condition']

const prompt = (overrides: Record<string, unknown>) => ({
  id: 'seed-de',
  code: 'vehicle_listing',
  language: 'de',
  title: 'Inseratetext für ein Fahrzeug',
  systemPrompt: 'Vorgabe: sachlich.',
  userPrompt: 'Fahrzeug: {{ vehicle.label }}',
  source: 'platform_seed',
  overriddenFromId: null,
  seedChanged: false,
  active: true,
  version: 1,
  updatedAt: '2026-11-15T10:00:00Z',
  ...overrides,
})

const SEEDS = ['de', 'fr', 'it', 'en'].map((language) => prompt({ id: `seed-${language}`, language }))

const library = (items: unknown[], connection: unknown = null) => ({
  items,
  codes: [{ code: 'vehicle_listing', placeholders: PLACEHOLDERS }],
  connection,
})

function install(items: unknown[], connection: unknown = null, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships\/dealership-1\/ai-prompts$/, handler: () => library(items, connection) },
  ])
}

afterEach(() => {
  cleanup()
})

const instructions = () => screen.getByRole('textbox', { name: (n) => n.startsWith(i18n.t('aiPrompts.fields.systemPrompt')) })

describe('AiAdministrationPage (KAN-227)', () => {
  it('says no AI provider is connected and links the integrations', async () => {
    install(SEEDS)
    renderWithProviders(<AiAdministrationPage />, { route: '/administration/ai' })
    expect(await screen.findByText(i18n.t('aiPrompts.connection.none'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('aiPrompts.connection.manage') })).toHaveAttribute('href', '/administration/integrations')
    expect(screen.getByText(i18n.t('aiPrompts.status.default'))).toBeInTheDocument()
    expect(instructions()).toHaveValue('Vorgabe: sachlich.')
  })

  it('names the connection in use', async () => {
    install(SEEDS, { connectionId: 'c-1', displayName: 'Unser OpenAI', providerCode: 'openai', providerName: 'OpenAI', status: 'connected' })
    renderWithProviders(<AiAdministrationPage />, { route: '/administration/ai' })
    expect(await screen.findByText('Unser OpenAI')).toBeInTheDocument()
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
  })

  it('adapting the default creates the Betrieb’s prompt for that language', async () => {
    const user = userEvent.setup()
    const backend = install(SEEDS, null, [
      { method: 'POST', match: /^\/dealerships\/dealership-1\/ai-prompts$/, handler: (req) => ({ __status: 201, body: prompt({ ...(req.body as object), id: 'own-de', source: 'dealer' }) }) },
    ])
    renderWithProviders(<AiAdministrationPage />, { route: '/administration/ai' })
    await screen.findByText(i18n.t('aiPrompts.status.default'))

    await user.clear(instructions())
    await user.type(instructions(), 'Unser Stil.')
    await user.click(screen.getByRole('button', { name: i18n.t('aiPrompts.actions.save') }))

    await waitFor(() => expect(backend.callsTo(/\/ai-prompts$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/ai-prompts$/, 'POST')
    expect(call.body).toEqual({ code: 'vehicle_listing', language: 'de', systemPrompt: 'Unser Stil.', userPrompt: 'Fahrzeug: {{ vehicle.label }}' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a changed default is flagged, marked reviewed with If-Match, and the adaptation can go back to the default', async () => {
    const user = userEvent.setup()
    const own = prompt({ id: 'own-de', source: 'dealer', overriddenFromId: 'seed-de', systemPrompt: 'Unser Stil.', seedChanged: true, version: 3 })
    const backend = install([...SEEDS, own], null, [
      { method: 'PATCH', match: /^\/dealerships\/dealership-1\/ai-prompts\/own-de$/, handler: () => ({ ...own, seedChanged: false, version: 4 }) },
      { method: 'DELETE', match: /^\/dealerships\/dealership-1\/ai-prompts\/own-de$/, handler: () => ({ __status: 204 }) },
    ])
    renderWithProviders(<AiAdministrationPage />, { route: '/administration/ai' })

    expect(await screen.findByText(i18n.t('aiPrompts.seedChanged.title'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('aiPrompts.status.adapted'))).toBeInTheDocument()
    expect(instructions()).toHaveValue('Unser Stil.')

    await user.click(screen.getByRole('button', { name: i18n.t('aiPrompts.seedChanged.markReviewed') }))
    await waitFor(() => expect(backend.callsTo(/own-de$/, 'PATCH')).toHaveLength(1))
    const [review] = backend.callsTo(/own-de$/, 'PATCH')
    expect(review.body).toEqual({ seedReviewed: true })
    expect(review.headers.get('If-Match')).toBe('3')

    await user.click(screen.getByRole('button', { name: i18n.t('aiPrompts.actions.backToDefault') }))
    await waitFor(() => expect(backend.callsTo(/own-de$/, 'DELETE')).toHaveLength(1))
    expect(backend.callsTo(/own-de$/, 'DELETE')[0].headers.get('If-Match')).toBe('3')
  })
})
