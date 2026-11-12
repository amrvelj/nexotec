// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { formatDate } from '../../utils/format'
import { IdentityTab } from './IdentityTab'
import type { VehicleMdmRead, VehiclePartyAllocationRead } from '../../api/types'

// KAN-644 (Anto, 2026-11-12, A): one current holder; a holder from a later
// date is listed as upcoming «ab <date>». KAN-583 (2A): «Eintrag beenden» in
// each party row's menu; (1A) a cancelled planned entry reads «planned,
// never started» among the former parties.

const vehicle: VehicleMdmRead = {
  id: '01a0af73-6570-7571-9170-ec0f5b17f9ac',
  vehicleNumber: 'F-000002',
  vin: 'WBA4Y9F55LCE89GLA',
  stammnummer: null,
  typeApprovalNumber: null,
  catalogueVariantId: null,
  mergedIntoVehicleId: null,
  catalogueMatchStatus: 'unverified',
  vehicleStatus: 'active',
  firstRegistrationDate: null,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

const HANDOVER = new Date(Date.now() + 19 * 86_400_000).toISOString()

function party(over: Partial<VehiclePartyAllocationRead>): VehiclePartyAllocationRead {
  return {
    id: 'p1', vehicleId: vehicle.id, customerId: 'c-clara', role: 'owner',
    effectiveFrom: '2026-01-01T00:00:00Z', effectiveTo: HANDOVER, displayName: 'Clara Muster', ...over,
  }
}

const meier = party({ id: 'p2', customerId: 'c-meier', displayName: 'Anna Meier', effectiveFrom: HANDOVER, effectiveTo: null })

function renderTab(props: { parties: VehiclePartyAllocationRead[]; formerParties?: VehiclePartyAllocationRead[]; onEndEntry?: (p: VehiclePartyAllocationRead) => Promise<void>; canEndEntry?: boolean }) {
  return renderWithProviders(
    <IdentityTab
      vehicle={vehicle}
      parties={props.parties}
      formerParties={props.formerParties ?? []}
      onSaveField={async () => {}}
      onReload={() => {}}
      onAllocate={async () => {}}
      allocating={false}
      onAllocatingChange={() => {}}
      customerCandidates={[]}
      customerSearch=""
      onCustomerSearchChange={() => {}}
      onCustomerOverlayClose={() => {}}
      canEndEntry={props.canEndEntry ?? true}
      onEndEntry={props.onEndEntry ?? (async () => {})}
    />,
  )
}

describe('IdentityTab — a planned handover (KAN-644, KAN-583)', () => {
  beforeEach(() => {
    installFakeBackend([])
    void i18n.changeLanguage('de')
  })

  it('lists the current owner first and the future owner as upcoming «ab <date>»', () => {
    renderTab({ parties: [meier, party({})] })

    const upcoming = i18n.t('vehicleDetail.parties.upcoming', { date: formatDate(HANDOVER, 'de-CH') })
    const names = screen.getAllByRole('button', { name: /Clara Muster|Anna Meier/ }).map((b) => b.textContent)
    expect(names).toEqual(['Clara Muster', 'Anna Meier'])
    // The «ab» badge sits in Mrs. Meier's row, not Clara's.
    let row: HTMLElement | null = screen.getByRole('button', { name: 'Anna Meier' })
    while (row && !row.textContent?.includes(upcoming)) row = row.parentElement
    expect(row?.textContent).not.toContain('Clara Muster')
    expect(screen.getAllByText(upcoming)).toHaveLength(1) // Clara is not marked upcoming
  })

  it('«Eintrag beenden» in a row menu ends that entry', async () => {
    const ended: string[] = []
    renderTab({ parties: [party({}), meier], onEndEntry: async (p) => void ended.push(p.id) })

    const menus = screen.getAllByRole('button', { name: i18n.t('vehicleDetail.parties.entryMenu') })
    await userEvent.click(menus[1])
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('vehicleDetail.parties.endEntry') }))
    expect(ended).toEqual(['p2'])
  })

  it('offers no row menu to a session that may not write vehicle links', () => {
    renderTab({ parties: [party({})], canEndEntry: false })
    expect(screen.queryByRole('button', { name: i18n.t('vehicleDetail.parties.entryMenu') })).toBeNull()
  })

  it('a refused end says so in the user\'s language', async () => {
    renderTab({ parties: [party({})], onEndEntry: async () => { throw new Error('boom') } })
    await userEvent.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.parties.entryMenu') }))
    await userEvent.click(await screen.findByRole('menuitem', { name: i18n.t('vehicleDetail.parties.endEntry') }))
    expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('vehicleDetail.parties.endEntryError'))
  })

  it('a cancelled planned entry reads «planned, never started» among the former parties', async () => {
    const cancelled = party({ id: 'p3', customerId: 'c-meier', displayName: 'Anna Meier', effectiveFrom: HANDOVER, effectiveTo: HANDOVER })
    renderTab({ parties: [party({ effectiveTo: null })], formerParties: [cancelled] })
    await userEvent.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.parties.showFormer') }))
    let row: HTMLElement | null = screen.getByRole('button', { name: 'Anna Meier' })
    while (row && !row.textContent?.includes(i18n.t('vehicleDetail.parties.plannedNeverStarted'))) row = row.parentElement
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).queryByText('Clara Muster')).toBeNull()
  })
})
