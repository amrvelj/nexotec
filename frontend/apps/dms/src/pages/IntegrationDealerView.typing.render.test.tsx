// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-249 — the connect dialog's config and secret inputs and the rotate
// dialog's secret inputs read `e.currentTarget.value` inside a functional
// state updater. React may run that updater after the event has finished
// dispatching, when `currentTarget` is null, so the keystroke throws or is
// lost. Each field must show exactly what was typed into it.

const PROVIDER = {
  id: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', category: 'vehicle_data', authType: 'oauth2_user_enrollment',
  capabilityCodes: [], requiredConfigKeys: ['clientId'], requiredSecretSlots: ['client_secret'], supportsSandbox: true,
  docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

const CONNECTION = {
  id: 'conn-1', providerId: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', environment: 'sandbox',
  status: 'connected', enabled: true, config: {}, lastVerifiedAt: null, lastError: null, version: 1,
  secretSlots: [{ slot: 'client_secret', rotatedAt: null }],
} as unknown as IntegrationConnectionRead

function install({ connected }: { connected: boolean }) {
  installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: connected ? [CONNECTION] : [], nextCursor: null, total: 0, totalIsEstimate: false }),
    },
  ])
}

afterEach(() => cleanup())

describe('IntegrationDealerView — every settings input keeps what is typed (KAN-249)', () => {
  it('the connect dialog keeps the typed config value and secret', async () => {
    const user = userEvent.setup()
    install({ connected: false })
    renderWithProviders(<IntegrationDealerView />)

    await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    const dialog = await screen.findByRole('dialog')
    const clientId = within(dialog).getByLabelText(i18n.t('integrationEnums.configKey.clientId'))
    const secret = within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret'))

    await user.type(clientId, 'garage-nord')
    await user.type(secret, 's3cret!')

    expect(clientId).toHaveValue('garage-nord')
    expect(secret).toHaveValue('s3cret!')
  })

  it('the rotate dialog keeps the typed secret', async () => {
    const user = userEvent.setup()
    install({ connected: true })
    renderWithProviders(<IntegrationDealerView />)

    await user.click(await screen.findByRole('button', { name: i18n.t('common.rowActionsLabel') }))
    await user.click(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.rotateSecret') }))
    const dialog = await screen.findByRole('dialog')
    const secret = within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret'))

    await user.type(secret, 'n3w-s3cret')

    expect(secret).toHaveValue('n3w-s3cret')
  })
})
