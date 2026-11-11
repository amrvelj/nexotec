import { describe, expect, it } from 'vitest'
import type { IntegrationConnectionRead } from '../api/types'
import { buildConnectionRowMenu, settableSecretSlots } from './connectionRowMenu'

// KAN-603 — the one builder both the dealer card and the platform grid call:
// given the provider's secret slots, «Secret setzen» is enabled on a
// connection that has none stored, on either surface.

const t = (key: string) => key
const noop = () => {}
const actions = { onTest: noop, onViewUsage: noop, onRotateSecret: noop, onToggleEnabled: noop, onDelete: noop }

function connection(slots: string[]): Pick<IntegrationConnectionRead, 'enabled' | 'secretSlots'> {
  return { enabled: true, secretSlots: slots.map((slot) => ({ slot, rotatedAt: null })) } as unknown as Pick<
    IntegrationConnectionRead,
    'enabled' | 'secretSlots'
  >
}

const secretItem = (groups: ReturnType<typeof buildConnectionRowMenu>) => groups.edit?.[2]

describe('buildConnectionRowMenu (KAN-603)', () => {
  it('enables «Secret setzen» when the provider has a slot and none is stored', () => {
    const item = secretItem(buildConnectionRowMenu(t, connection([]), actions, ['client_secret']))
    expect(item?.label).toBe('integrationsList.actions.setSecret')
    expect(item?.disabled).toBe(false)
  })

  it('reads «Secret erneuern» once a slot is stored', () => {
    const item = secretItem(buildConnectionRowMenu(t, connection(['client_secret']), actions, ['client_secret']))
    expect(item?.label).toBe('integrationsList.actions.rotateSecret')
    expect(item?.disabled).toBe(false)
  })

  it('disables it, with its reason, only for a provider without slots', () => {
    const item = secretItem(buildConnectionRowMenu(t, connection([]), actions, []))
    expect(item?.disabled).toBe(true)
    expect(item?.disabledReason).toBe('integrationsList.actions.rotateSecretDisabledReason')
  })

  it('always offers «Verbindung löschen» as the last destructive item', () => {
    const groups = buildConnectionRowMenu(t, connection([]), actions, [])
    expect(groups.destructive?.map((i) => i.label)).toEqual([
      'integrationsList.actions.disable',
      'integrationsList.actions.delete',
    ])
  })

  it('settableSecretSlots joins the required slots and the stored ones', () => {
    expect(settableSecretSlots(connection(['password']), ['client_secret'])).toEqual(['client_secret', 'password'])
  })
})
