// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen } from '@testing-library/react'
import i18n from '../i18n'
import de from '../i18n/locales/de'
import en from '../i18n/locales/en'
import fr from '../i18n/locales/fr'
import it_ from '../i18n/locales/it'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// KAN-515 — the page told every user «PLATFORM_ADMIN» in a badge, and its
// 403 texts named the role code too. A role code is not a word a dealer
// understands; next to the read-only line it read as if the user were
// platform staff. The badge now says who maintains the lists, in the UI
// language, and no text on the screen carries a role code.

const DIESEL = {
  id: 'v-diesel',
  listCode: 'fuel_type',
  valueCode: 'diesel',
  labelDe: 'Diesel',
  labelFr: 'Gazole',
  labelIt: 'Gasolio',
  labelEn: 'Diesel fuel',
  active: true,
  sortOrder: 0,
  version: 1,
}

const DEALER: FakeRoute = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'u-1', firstName: 'Mia', lastName: 'Muster', email: 'mia@example.ch', accessRoles: ['sales'], isDealerManager: true },
    activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
    memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ['customers'],
  }),
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('ReferenceDataPage — no role code on the screen (KAN-515)', () => {
  it.each(['de', 'fr', 'it', 'en'])('says who maintains the lists in %s, never «platform_admin»', async (language) => {
    await i18n.changeLanguage(language)
    installFakeBackend([
      DEALER,
      { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => ({ items: [DIESEL], nextCursor: null }) },
    ])

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    expect(await screen.findByText('Gazole')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('referenceData.maintainedBy'))).toBeInTheDocument()
    expect(i18n.t('referenceData.maintainedBy')).toContain('Nexotec')
    expect(document.body.textContent).not.toMatch(/platform_admin/i)
  })

  it('says a refused read without a role code', async () => {
    installFakeBackend([
      DEALER,
      {
        method: 'GET',
        match: /\/reference-data\/fuel_type$/,
        handler: () => status(403, { error: { code: 'forbidden', message: 'platform_admin only' } }),
      },
    ])

    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    expect(await screen.findByText(i18n.t('referenceData.forbidden.title'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('referenceData.forbidden.body'))).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/platform_admin/i)
  })

  it('carries no role code in any bundle’s reference-data texts', () => {
    for (const bundle of [de, fr, it_, en]) {
      expect(JSON.stringify(bundle.referenceData)).not.toMatch(/platform_admin/i)
    }
  })
})
