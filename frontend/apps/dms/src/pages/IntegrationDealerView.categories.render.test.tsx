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
      supportsSandbox: false, platformOnly: false,
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

// KAN-787 (Anto, 2026-11-22: option B) — a provider Nexotec connects once for
// every Betrieb (`platformOnly`: the mailbox providers, Google Business
// Profile) is the backend's to refuse for a dealer, so the dealer view does
// not offer it. A category left with no provider disappears with it.
function provider(id: string, displayName: string, category: string, platformOnly: boolean) {
  return {
    id, providerCode: id, displayName, category, authType: 'oauth2', capabilityCodes: [],
    requiredConfigKeys: [], requiredSecretSlots: [], supportsSandbox: false, platformOnly, docsUrl: null, version: 1,
  }
}

describe('IntegrationDealerView — platform-only providers are not offered to a dealer (KAN-787)', () => {
  it('hides the mailbox providers and Google Business Profile, and the category left empty', async () => {
    await i18n.changeLanguage('de')
    installFakeBackend([
      {
        match: /^\/integrations\/providers$/,
        handler: () => ({
          items: [
            provider('autoscout24', 'AutoScout24', 'marketplace', false),
            provider('google_business_profile', 'Google Business Profile', 'marketplace', true),
            provider('google_workspace_mail', 'Google Workspace (Gmail)', 'calendar_mail', true),
            provider('microsoft_365_mail', 'Microsoft 365 (Mail)', 'calendar_mail', true),
          ],
        }),
      },
      { match: /^\/integrations\/connections/, handler: () => connections() },
    ])
    renderWithProviders(<IntegrationDealerView />)

    expect(await screen.findByText('AutoScout24')).toBeInTheDocument()
    expect(screen.getByText('Marktplatz')).toBeInTheDocument()
    expect(screen.queryByText('Google Business Profile')).not.toBeInTheDocument()
    expect(screen.queryByText('Google Workspace (Gmail)')).not.toBeInTheDocument()
    expect(screen.queryByText('Microsoft 365 (Mail)')).not.toBeInTheDocument()
    expect(screen.queryByText('Kalender und E-Mail')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Verbinden/ })).toHaveLength(1)
  })
})
