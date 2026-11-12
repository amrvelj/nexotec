// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationPlatformView } from './IntegrationPlatformView'

// KAN-650 — on the platform grid «Nutzung anzeigen» and «Secret erneuern»
// were wired to no-op handlers: enabled, and clicking them did nothing. They
// now open the same dialogs as the dealer card (ADR-061: the row menu is the
// one definition of what can be done to a connection).

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const PROVIDER = {
  id: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', category: 'vehicle_data',
  authType: 'oauth2_user_enrollment', capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: ['client_secret'],
  supportsSandbox: true, docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

const CONNECTION = {
  id: 'conn-1', tenantId: 'dealership-1', providerId: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat Garage Nord',
  environment: 'sandbox', status: 'connected', enabled: true, config: {}, lastVerifiedAt: null, lastError: null,
  secretSlots: [{ slot: 'client_secret', rotatedAt: null }], version: 1,
} as unknown as IntegrationConnectionRead

function install() {
  const puts: string[] = []
  installFakeBackend([
    { match: /^\/dealerships/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
    { match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
    {
      match: /^\/integrations\/connections\/conn-1\/usage$/,
      handler: () => ({ callsThisPeriod: 4200, costUnitsThisPeriod: '17.25', indicative: true }),
    },
    {
      method: 'PUT',
      match: /^\/integrations\/connections\/conn-1\/secrets\/client_secret$/,
      handler: (request) => {
        puts.push(request.method)
        return {}
      },
    },
    { match: /^\/integrations\/connections/, handler: () => ({ items: [CONNECTION], nextCursor: null, total: 1, totalIsEstimate: false }) },
  ])
  return puts
}

async function openRowMenuItem(name: string) {
  const user = userEvent.setup()
  renderWithProviders(<IntegrationPlatformView />)
  await screen.findByText('auto-i-dat Garage Nord')
  await user.click(await screen.findByRole('button', { name: i18n.t('common.rowActionsLabel') }))
  await user.click(await screen.findByRole('menuitem', { name }))
  return { user, dialog: await screen.findByRole('dialog') }
}

describe('IntegrationPlatformView row menu (KAN-650)', () => {
  it('«Nutzung anzeigen» opens the usage dialog with the connection\'s figures', async () => {
    install()
    const { dialog } = await openRowMenuItem(i18n.t('integrationsList.actions.viewUsage'))

    expect(within(dialog).getByText(i18n.t('integrationsList.usageModal.title', { name: 'auto-i-dat Garage Nord' }))).toBeInTheDocument()
    expect(await within(dialog).findByText("4'200")).toBeInTheDocument()
    expect(within(dialog).getByText('17.2500')).toBeInTheDocument()
  })

  it('«Secret erneuern» opens the secret dialog and writes the slot', async () => {
    const puts = install()
    const { user, dialog } = await openRowMenuItem(i18n.t('integrationsList.actions.rotateSecret'))

    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret')), 'n3w-s3cret')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.rotateSecret') }))

    await vi.waitFor(() => expect(puts).toEqual(['PUT']))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
