// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useParams } from 'react-router-dom'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeBackend } from '../test/fakeBackend'
import type { VehicleMdmRead, VehicleSearchResult } from '../api/types'
import { DmsShell } from './DmsShell'

// KAN-82 — FR-V-06's second entry point: "a plate typed here resolves
// through the same rules as FR-V-06, ambiguity picker included; global
// search never guesses either" (FR-UI-08). Global search used to read only
// `filtered`, which came back as an unrelated page next to every resolved
// identifier: a plate typed here showed other people's cars.

const tr = (key: string) => i18n.getFixedT('en')(key)

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
const BMW = vehicle('veh-bmw', 'WBA3A5C51CF256985', 'F-000003')

/** The server's answers, keyed by `q` — the shape of each one is what
 * `GET /vehicle-mdm/search` returns for that kind of string (KAN-82). */
const SEARCH: Record<string, VehicleSearchResult> = {
  'ZH 12345': { resolved: { ...TARGET, currentPlate: 'ZH 12345' }, pickerCandidates: [], filtered: EMPTY_PAGE },
  'TG 41277': {
    resolved: null,
    pickerCandidates: [
      { id: TARGET.id, vehicleNumber: TARGET.vehicleNumber, vin: TARGET.vin, plate: 'TG 41277', plateGroupId: 'grp-1', isConflict: false },
      { id: PAIR.id, vehicleNumber: PAIR.vehicleNumber, vin: PAIR.vin, plate: 'TG 41277', plateGroupId: 'grp-1', isConflict: false },
    ],
    filtered: EMPTY_PAGE,
  },
  'VD 178204': {
    resolved: null,
    pickerCandidates: [
      { id: TARGET.id, vehicleNumber: TARGET.vehicleNumber, vin: TARGET.vin, plate: 'VD 178204', plateGroupId: null, isConflict: true },
      { id: PAIR.id, vehicleNumber: PAIR.vehicleNumber, vin: PAIR.vin, plate: 'VD 178204', plateGroupId: null, isConflict: true },
    ],
    filtered: EMPTY_PAGE,
  },
  'AG 55555': { resolved: null, pickerCandidates: [], filtered: EMPTY_PAGE },
  WBA: { resolved: null, pickerCandidates: [], filtered: { items: [BMW], nextCursor: null, total: 1, totalIsEstimate: false } },
}

function VehicleRoute() {
  const { id } = useParams()
  return <div>vehicle page {id}</div>
}

function renderShell(customers: { items: unknown[] } = { items: [] }): FakeBackend {
  const backend = installFakeBackend([
    {
      method: 'GET',
      match: /\/me\/preferences\/ui$/,
      handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage: 'en', density: 'default' } }),
    },
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: {
          id: 'user-1', dealershipId: 'd-1', firstName: 'Test', lastName: 'Advisor', email: 'advisor@example.ch',
          phone: null, role: 'service_advisor', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'active',
          authIdentityId: 'auth-1', status: 'active', version: 1,
          createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
        },
        activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
        memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
      }),
    },
    { method: 'GET', match: /\/customers$/, handler: () => ({ ...customers, nextCursor: null }) },
    { method: 'GET', match: /\/vehicle-mdm\/search$/, handler: (req) => SEARCH[req.params.get('q') ?? ''] },
  ])
  renderWithProviders(
    <DmsShell>
      <Routes>
        <Route path="/vehicles/:id" element={<VehicleRoute />} />
        <Route path="*" element={<div />} />
      </Routes>
    </DmsShell>,
  )
  return backend
}

async function search(query: string) {
  await userEvent.click(await screen.findByRole('button', { name: tr('shell.globalSearch.placeholder') }))
  await userEvent.type(screen.getByRole('combobox'), query)
}

afterEach(async () => {
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell global search — identifiers resolve as on the Vehicles screen (KAN-82)', () => {
  it('a full plate shows the resolved vehicle with its plate, and opens it', async () => {
    renderShell()
    await search('ZH 12345')

    const options = await screen.findAllByRole('option')
    expect(options).toHaveLength(1)
    expect(within(options[0]).getByText(TARGET.vin)).toBeInTheDocument()
    expect(within(options[0]).getByText(TARGET.vehicleNumber)).toBeInTheDocument()
    expect(within(options[0]).getByText('ZH 12345')).toBeInTheDocument()
    expect(screen.getByText(tr('shell.nav.vehicles'))).toBeInTheDocument()

    await userEvent.click(options[0])
    expect(await screen.findByText(`vehicle page ${TARGET.id}`)).toBeInTheDocument()
  })

  it('a resolved identifier leads, above any customer match', async () => {
    renderShell({
      items: [{ id: 'cust-1', customerNumber: 'K-000123', customerType: 'person', firstName: 'Anna', lastName: 'Meier', address: null }],
    })
    await search('ZH 12345')

    const vehicles = await screen.findByText(tr('shell.nav.vehicles'))
    const customers = screen.getByText(tr('shell.nav.customers'))
    expect(vehicles.compareDocumentPosition(customers) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a Wechselschild plate offers both cars under the Wechselschild heading, never guessing', async () => {
    renderShell()
    await search('TG 41277')

    expect(await screen.findByText(tr('vehiclesList.picker.wechselschildTitle'))).toBeInTheDocument()
    expect(screen.queryByText(tr('vehiclesList.picker.conflictTitle'))).not.toBeInTheDocument()
    const options = screen.getAllByRole('option')
    expect(options.map((o) => within(o).getByText(/^F-/).textContent)).toEqual([TARGET.vehicleNumber, PAIR.vehicleNumber])

    await userEvent.click(options[1])
    expect(await screen.findByText(`vehicle page ${PAIR.id}`)).toBeInTheDocument()
  })

  it('a conflicting plate offers both cars under the conflict heading', async () => {
    renderShell()
    await search('VD 178204')

    expect(await screen.findByText(tr('vehiclesList.picker.conflictTitle'))).toBeInTheDocument()
    expect(screen.queryByText(tr('vehiclesList.picker.wechselschildTitle'))).not.toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(2)
  })

  it('a plate that matches no vehicle reads as no match, not as other cars', async () => {
    renderShell()
    await search('AG 55555')

    expect(await screen.findByText(tr('shell.globalSearch.noResults'))).toBeInTheDocument()
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })

  it('a brand fragment still filters as before', async () => {
    renderShell()
    await search('WBA')

    const options = await screen.findAllByRole('option')
    expect(options).toHaveLength(1)
    expect(within(options[0]).getByText(BMW.vin)).toBeInTheDocument()
    expect(screen.getByText(tr('shell.nav.vehicles'))).toBeInTheDocument()
  })
})
