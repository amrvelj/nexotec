// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'
import { LANGUAGE_ENDONYMS } from '../i18n/languageEndonyms'

// KAN-343 (Anto, 2026-11-03, option A): the reference-data columns name each
// language in itself, whatever the user's UI language — a French or Italian
// admin reads «Deutsch», never «Allemand» / «Tedesco».

const emptyValuesRoute: FakeRoute = {
  method: 'GET',
  match: /\/reference-data\//,
  handler: () => ({ items: [], nextCursor: null }),
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ReferenceDataPage — language columns are endonyms (KAN-343)', () => {
  it.each(['de', 'fr', 'it', 'en'] as const)('in %s the four column headers are Deutsch, Français, Italiano, English', async (lng) => {
    await i18n.changeLanguage(lng)
    installFakeBackend([emptyValuesRoute])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const headers = await screen.findAllByRole('columnheader')
    const names = headers.map((h) => h.textContent)
    expect(names).toEqual(expect.arrayContaining(['Deutsch', 'Français', 'Italiano', 'English']))
    expect(Object.values(LANGUAGE_ENDONYMS)).toEqual(['Deutsch', 'Français', 'Italiano', 'English'])
  })
})
