// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { CustomersListPage } from './CustomersListPage'

// KAN-321 — the grid's load-failure line was the hardcoded English "Failed
// to load customers." under every UI language.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('CustomersListPage — a failed load is reported in the UI language (KAN-321)', () => {
  it('says so in French under the French UI, never in hardcoded English', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { match: /^\/customers$/, handler: () => status(500, { error: { code: 'internal', message: 'boom', details: null } }) },
    ])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    // Hardcoded expectation, not i18n.t(): a key missing on both sides must fail here.
    expect(await screen.findByText('Impossible de charger les clients.')).toBeInTheDocument()
    expect(screen.queryByText('Failed to load customers.')).not.toBeInTheDocument()
  })
})
