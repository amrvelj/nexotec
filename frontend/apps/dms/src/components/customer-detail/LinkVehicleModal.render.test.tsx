// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { VehicleMdmRead, VehicleSearchResult } from '../../api/types'
import { LinkVehicleModal } from './LinkVehicleModal'

// KAN-82 — the vehicle search answers a full identifier with `resolved`
// (or `pickerCandidates`) and an empty `filtered` page. This dialog read
// only `filtered`, so a VIN, vehicle number or plate typed here found
// nothing at all.

function vehicle(id: string, vin: string, vehicleNumber: string): VehicleMdmRead {
  return {
    id, vin, vehicleNumber, stammnummer: null, typeApprovalNumber: null, firstRegistrationDate: null,
    catalogueVariantId: null, catalogueMatchStatus: 'unverified', vehicleStatus: 'active',
    mergedIntoVehicleId: null, version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  }
}

const EMPTY_PAGE = { items: [], nextCursor: null, total: 0, totalIsEstimate: false }
const TARGET = vehicle('veh-target', 'ZAR94000007123456', 'F-000001')
const PAIR = vehicle('veh-pair', 'WVWZZZ1JZXW000001', 'F-000002')

const SEARCH: Record<string, VehicleSearchResult> = {
  'ZH 12345': { resolved: { ...TARGET, currentPlate: 'ZH 12345' }, pickerCandidates: [], filtered: EMPTY_PAGE },
  'TG 41277': {
    resolved: null,
    pickerCandidates: [
      { id: TARGET.id, vehicleNumber: TARGET.vehicleNumber, vin: TARGET.vin, plate: 'TG 41277', plateGroupId: 'g', isConflict: false },
      { id: PAIR.id, vehicleNumber: PAIR.vehicleNumber, vin: PAIR.vin, plate: 'TG 41277', plateGroupId: 'g', isConflict: false },
    ],
    filtered: EMPTY_PAGE,
  },
  WVW: { resolved: null, pickerCandidates: [], filtered: { items: [PAIR], nextCursor: null, total: 1, totalIsEstimate: false } },
}

function renderModal() {
  const backend = installFakeBackend([
    { method: 'GET', match: /^\/vehicle-mdm\/search$/, handler: (req) => SEARCH[req.params.get('q') ?? ''] },
    { method: 'POST', match: /^\/customers\/cust-1\/vehicles$/, handler: () => ({}) },
  ])
  renderWithProviders(<LinkVehicleModal opened onClose={() => {}} onLinked={() => {}} customerId="cust-1" />)
  return backend
}

async function type(query: string) {
  await userEvent.type(await screen.findByLabelText(i18n.t('customerDetail.linkVehicle.search')), query)
}

afterEach(() => cleanup())

describe('LinkVehicleModal — a full identifier finds its vehicle (KAN-82)', () => {
  it('a full plate offers the resolved vehicle and links it', async () => {
    const backend = renderModal()
    await type('ZH 12345')

    await userEvent.click(await screen.findByRole('button', { name: /F-000001 — ZAR94000007123456 — ZH 12345/ }))
    await userEvent.click(screen.getByRole('button', { name: i18n.t('customerDetail.linkVehicle.confirm') }))

    await waitFor(() => expect(backend.callsTo(/^\/customers\/cust-1\/vehicles$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/^\/customers\/cust-1\/vehicles$/, 'POST')[0].body).toEqual({ vehicleId: TARGET.id, role: 'owner' })
  })

  it('a shared plate offers both cars and preselects neither', async () => {
    renderModal()
    await type('TG 41277')

    expect(await screen.findByRole('button', { name: /F-000001 — ZAR94000007123456 — TG 41277/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /F-000002 — WVWZZZ1JZXW000001 — TG 41277/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('customerDetail.linkVehicle.confirm') })).toBeDisabled()
  })

  it('a fragment still lists the filtered vehicles', async () => {
    renderModal()
    await type('WVW')

    expect(await screen.findByRole('button', { name: /F-000002 — WVWZZZ1JZXW000001/ })).toBeInTheDocument()
  })
})
