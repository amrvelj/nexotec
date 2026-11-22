// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-227 — the Betrieb connects its own AI provider (bring your own key): the
// card sits under «KI-Anbieter», the dialog asks the model and the API key,
// the connection is production (an AI key has no sandbox) and is tested on
// connect.

const PROVIDER = {
  id: 'prov-ai', providerCode: 'openai', displayName: 'OpenAI', category: 'ai', authType: 'api_key',
  capabilityCodes: ['ai_text_generation'], requiredConfigKeys: ['model'], requiredSecretSlots: ['api_key'],
  supportsSandbox: false, platformOnly: false, docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

const CREATED = {
  id: 'conn-ai', providerId: 'prov-ai', providerCode: 'openai', displayName: 'OpenAI', environment: 'production',
  status: 'not_configured', enabled: true, config: { model: 'dealer-model' }, lastVerifiedAt: null, lastError: null, version: 1,
} as unknown as IntegrationConnectionRead

afterEach(() => cleanup())

describe('IntegrationDealerView — an AI provider (KAN-227)', () => {
  it('connects on production with the model and the key, then tests the connection once', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
      { method: 'GET', match: /^\/integrations\/connections$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
      { method: 'POST', match: /^\/integrations\/connections$/, handler: () => ({ __status: 201, body: CREATED }) },
      { method: 'PUT', match: /^\/integrations\/connections\/conn-ai\/secrets\/api_key$/, handler: () => ({ slot: 'api_key', rotatedAt: null }) },
      { method: 'POST', match: /^\/integrations\/connections\/conn-ai\/test$/, handler: () => ({ ...CREATED, status: 'connected' }) },
    ])
    renderWithProviders(<IntegrationDealerView />)

    expect(await screen.findByText(i18n.t('integrationsList.categories.ai'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.configKey.model')), 'dealer-model')
    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.api_key')), 'sk-own-key')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))

    await waitFor(() => expect(backend.callsTo(/\/conn-ai\/test$/, 'POST')).toHaveLength(1))
    const [create] = backend.callsTo(/^\/integrations\/connections$/, 'POST')
    expect(create.body).toMatchObject({ providerId: 'prov-ai', environment: 'production', config: { model: 'dealer-model' } })
    expect(backend.callsTo(/\/secrets\/api_key$/, 'PUT')[0].body).toEqual({ secretValue: 'sk-own-key' })
  })
})
