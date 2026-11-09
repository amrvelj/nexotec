// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-489 — the auto-i-dat test access is a Sandbox connection. The Connect
// dialog reminds the dealer what its agreement allows (a service account,
// test environments only) as the environment field's hint; the gateway
// refuses such a connection on the production server.

function provider(providerCode: string, displayName: string): IntegrationProviderRead {
  return {
    id: `prov-${providerCode}`, providerCode, displayName, category: 'vehicle_data', authType: 'oauth2_user_enrollment',
    capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: [], supportsSandbox: true, docsUrl: null,
    version: 1,
  } as unknown as IntegrationProviderRead
}

function install(item: IntegrationProviderRead) {
  installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [item] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }),
    },
  ])
}

async function environmentField() {
  const user = userEvent.setup()
  renderWithProviders(<IntegrationDealerView />)
  await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
  const dialog = await screen.findByRole('dialog')
  const label = i18n.t('integrationsList.fields.environment')
  return within(dialog).getByRole('textbox', { name: (name) => name.startsWith(label) })
}

afterEach(() => cleanup())

describe('IntegrationDealerView — the auto-i-dat test access reminder (KAN-489)', () => {
  it('connecting auto-i-dat names what a Sandbox test access may do', async () => {
    install(provider('auto_i_dat', 'auto-i-dat'))
    expect(await environmentField()).toHaveAccessibleDescription(i18n.t('integrationsList.connectModal.testAccessHint'))
  })

  it('another provider carries no such reminder', async () => {
    install(provider('auto_i_dat_mock', 'auto-i-dat (Demo)'))
    expect(await environmentField()).not.toHaveAccessibleDescription(
      i18n.t('integrationsList.connectModal.testAccessHint'),
    )
  })
})
