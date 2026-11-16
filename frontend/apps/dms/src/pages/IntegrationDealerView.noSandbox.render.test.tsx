// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-741 — the connect dialog's environment for a provider that is not an AI
// provider: one without a sandbox (AutoScout24, DAT) starts on, and sends,
// production (KAN-227 fixed the default; the backend now refuses the sandbox
// with `provider_has_no_sandbox`); one with a sandbox still starts on sandbox.

function provider(supportsSandbox: boolean): IntegrationProviderRead {
  return {
    id: 'prov-m', providerCode: supportsSandbox ? 'auto_i_dat' : 'autoscout24', displayName: supportsSandbox ? 'auto-i-dat' : 'AutoScout24',
    category: supportsSandbox ? 'vehicle_data' : 'marketplace', authType: 'none',
    capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: [], supportsSandbox, docsUrl: null, version: 1,
  } as unknown as IntegrationProviderRead
}

function created(environment: string): IntegrationConnectionRead {
  return {
    id: 'conn-m', providerId: 'prov-m', providerCode: 'autoscout24', displayName: 'AutoScout24', environment,
    status: 'not_configured', enabled: true, config: {}, lastVerifiedAt: null, lastError: null, version: 1,
  } as unknown as IntegrationConnectionRead
}

afterEach(() => cleanup())

async function connect(supportsSandbox: boolean) {
  const user = userEvent.setup()
  const backend = installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [provider(supportsSandbox)] }) },
    { method: 'GET', match: /^\/integrations\/connections$/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { method: 'POST', match: /^\/integrations\/connections$/, handler: (call) => ({ __status: 201, body: created((call.body as { environment: string }).environment) }) },
    { method: 'POST', match: /^\/integrations\/connections\/conn-m\/test$/, handler: () => ({ ...created('production'), status: 'connected' }) },
  ])
  renderWithProviders(<IntegrationDealerView />)
  await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
  const dialog = await screen.findByRole('dialog')
  const environment = within(dialog)
    .getAllByLabelText(i18n.t('integrationsList.fields.environment'))
    .find((el): el is HTMLInputElement => el instanceof HTMLInputElement && el.type !== 'hidden')!
  const shown = environment.value
  await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
  await waitFor(() => expect(backend.callsTo(/^\/integrations\/connections$/, 'POST')).toHaveLength(1))
  return { shown, sent: (backend.callsTo(/^\/integrations\/connections$/, 'POST')[0].body as { environment: string }).environment }
}

describe('IntegrationDealerView — the connect dialog\'s environment (KAN-741)', () => {
  it('starts a provider without a sandbox on production and sends production', async () => {
    const { shown, sent } = await connect(false)
    expect(shown).toBe(i18n.t('integrationEnums.environment.production'))
    expect(sent).toBe('production')
  })

  it('still starts a provider with a sandbox on sandbox', async () => {
    const { shown, sent } = await connect(true)
    expect(shown).toBe(i18n.t('integrationEnums.environment.sandbox'))
    expect(sent).toBe('sandbox')
  })
})
