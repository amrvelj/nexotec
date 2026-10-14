// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { SalesListPage } from './SalesListPage'

// KAN-162 — the deals grid's load-failure line was the hardcoded English
// "Failed to load deals." under every UI language. KAN-321 moved it to
// salesList.loadError; this pins it, as CustomersListPage's test pins its own.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('SalesListPage — a failed load is reported in the UI language (KAN-162)', () => {
  it('says so in Italian under the Italian UI, never in hardcoded English', async () => {
    await i18n.changeLanguage('it')
    installFakeBackend([
      { match: /^\/sales\/deals$/, handler: () => status(500, { error: { code: 'internal', message: 'boom', details: null } }) },
    ])
    renderWithProviders(<SalesListPage />, { route: '/sales' })

    // Hardcoded expectation, not i18n.t(): a key missing on both sides must fail here.
    expect(await screen.findByText('Impossibile caricare offerte e contratti.')).toBeInTheDocument()
    expect(screen.queryByText('Failed to load deals.')).not.toBeInTheDocument()
  })
})
