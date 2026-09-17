// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionPage, IntegrationProviderPage } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-61 — KAN-27 seeded the AutoScout24 provider with category="marketplace"
// but never added `integrationsList.categories.marketplace` to any locale, so
// the category header rendered the loud "⚠ MISSING I18N KEY" marker instead
// of a real label — the i18n harness (parseMissingKeyHandler) working
// exactly as designed, correctly catching a genuinely missing translation.

const MARKER = /⚠ MISSING I18N KEY/

const providers = (): IntegrationProviderPage => ({
  items: [
    {
      id: 'prov-as24',
      providerCode: 'autoscout24',
      displayName: 'AutoScout24',
      category: 'marketplace',
      authType: 'api_key',
      capabilityCodes: [],
      requiredConfigKeys: [],
      requiredSecretSlots: [],
      supportsSandbox: false,
      docsUrl: null,
      version: 1,
    },
  ],
})

const connections = (): IntegrationConnectionPage => ({
  items: [],
  nextCursor: null,
  total: 0,
  totalIsEstimate: false,
})

function installBackend() {
  return installFakeBackend([
    { match: /^\/integrations\/providers$/, handler: () => providers() },
    { match: /^\/integrations\/connections/, handler: () => connections() },
  ])
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

// Hardcoded, not derived via i18n.t() — the assertion must not compute its
// expected value the same way the component does, or a key missing in
// BOTH places would render the identical marker on both sides and the
// test would pass trivially (caught during revert-verification: removing
// the fr key alone did not fail this test until these were hardcoded).
const EXPECTED_LABEL: Record<'de' | 'fr' | 'it' | 'en', string> = {
  de: 'Marktplatz',
  fr: 'Marketplace',
  it: 'Marketplace',
  en: 'Marketplace',
}

describe('IntegrationDealerView — every seeded provider category has a real label (KAN-61)', () => {
  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`renders the marketplace category header in ${lng}, never the missing-key marker`, async () => {
      await i18n.changeLanguage(lng)
      installBackend()
      const { container } = renderWithProviders(<IntegrationDealerView />)

      const label = await screen.findByText(EXPECTED_LABEL[lng])
      expect(label).toBeInTheDocument()
      expect(container.textContent).not.toMatch(MARKER)
    })
  }
})
