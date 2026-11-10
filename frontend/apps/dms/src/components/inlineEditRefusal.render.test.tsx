// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError } from '../api/client'
import i18n from '../i18n'
import fr from '../i18n/locales/fr'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import { isStaleVersion } from '../utils/apiRefusal'
import { OverviewTab } from './customer-detail/OverviewTab'
import { ExternalIdsTab } from './customer-detail/ExternalIdsTab'
import { IdentityTab } from './vehicle-detail/IdentityTab'
import { DetailsTab } from './stock-detail/DetailsTab'
import { ReferenceDataPage } from '../pages/ReferenceDataPage'
import type { StockItemRead, VehicleMdmRead } from '../api/types'

// KAN-473 — the kit's InlineEditField showed whatever its onSave threw by
// the error's `message`, and every screen passed the raw ApiError through:
// an inline-edit refusal read as the API's English text in a French UI. Each
// area's inline field now says it in the UI language (apiRefusalText), and
// only a stale If-Match 409 offers the reload — under a translated label.

// jsdom has no layout, so TanStack Virtual windows the External IDs grid down
// to zero rows; as in listRowActions.failure, it renders every row here.
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

const ENGLISH = 'The API said this in English.'
const refusal = (statusCode: number, details: Record<string, unknown> | null) =>
  new ApiError(statusCode, statusCode === 403 ? 'forbidden' : 'conflict', ENGLISH, details)

beforeEach(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(async () => {
  await i18n.changeLanguage('de')
})

async function editAndSave(currentValue: string, typed = 'X') {
  const user = userEvent.setup()
  await user.click(await screen.findByText(currentValue))
  // The editor opens focused; other inputs on the screen (search, filters)
  // are not the one being edited.
  await user.keyboard(`${typed}{Enter}`)
  return user
}

describe('an inline-edit refusal is said in the UI language, never the API English (KAN-473)', () => {
  it('customers: a stale save says so and offers the reload under its French label', async () => {
    installFakeBackend([{ match: /^\/customers\/advisor-options$/, handler: () => ({ items: [] }) }])
    const save = vi.fn().mockResolvedValue(undefined)
    const onReload = vi.fn()
    renderWithProviders(
      <OverviewTab
        customer={customer({ firstName: 'Hansruedi' })}
        phones={[]}
        emails={[]}
        onSaveField={vi.fn().mockRejectedValue(refusal(409, { currentVersion: 4, ifMatchVersion: 3 }))}
        isConflict={isStaleVersion}
        onReload={onReload}
        onSaveAddress={save}
        onCreatePhone={save}
        onUpdatePhone={save}
        onDeletePhone={save}
        onCreateEmail={save}
        onUpdateEmail={save}
        onDeleteEmail={save}
        countryOptions={[]}
        locale="fr-CH"
      />,
    )

    const user = await editAndSave('Hansruedi')

    expect(await screen.findByText(fr.apiRefusal.staleVersion)).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: fr.common.inlineEdit.reload }))
    expect(onReload).toHaveBeenCalledTimes(1)
  })

  it('customers: an external ID refused with a reason says the reason', async () => {
    installFakeBackend([])
    renderWithProviders(
      <ExternalIdsTab
        externalIds={[
          {
            id: 'x1',
            customerId: 'cust-1',
            systemName: 'CRM',
            externalId: 'A-17',
            createdAt: '2026-01-01T00:00:00Z',
            updatedAt: '2026-01-01T00:00:00Z',
          } as never,
        ]}
        loading={false}
        error={null}
        canWrite
        onCreate={vi.fn()}
        onUpdate={vi.fn().mockRejectedValue(refusal(409, { reason: 'external_id_taken', systemName: 'CRM', externalId: 'A-17X' }))}
        onDelete={vi.fn()}
        locale="fr-CH"
      />,
    )

    await editAndSave('A-17')

    expect(await screen.findByText(fr.apiRefusal.reasons.external_id_taken)).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).not.toBeInTheDocument()
  })

  it('vehicles: a refused VIN says why, with no reload for a refusal that is not a stale version', async () => {
    installFakeBackend([])
    const vehicle: VehicleMdmRead = {
      id: 'v1',
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
    renderWithProviders(
      <IdentityTab
        vehicle={vehicle}
        parties={[]}
        formerParties={[]}
        onSaveField={vi.fn().mockRejectedValue(new ApiError(422, 'unprocessable_entity', ENGLISH, { reason: 'invalid_vin' }))}
        onReload={vi.fn()}
        onAllocate={async () => {}}
        allocating={false}
        onAllocatingChange={() => {}}
        customerCandidates={[]}
        customerSearch=""
        onCustomerSearchChange={() => {}}
        onCustomerOverlayClose={() => {}}
      />,
    )

    await editAndSave('WBA4Y9F55LCE89GLA')

    expect(await screen.findByText(fr.common.invalidVin)).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: fr.common.inlineEdit.reload })).not.toBeInTheDocument()
  })

  it("stock: a role's refusal is said as such", async () => {
    installFakeBackend([])
    const NOW = '2026-10-01T09:00:00Z'
    const item = {
      id: 's1', stockNumber: 'ST-1001', vehicleLabel: 'VW Golf GTI', vin: null, vehicleId: null, condition: 'used',
      lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green', odometerKm: null, listPrice: null,
      effectivePrice: null, basePrice: null, bodyStyle: null, exteriorColour: null, firstRegistrationDate: null,
      inStockAt: NOW, leftStockAt: null, isInvoiceable: true, createdAt: NOW, updatedAt: NOW, version: 1,
    } as unknown as StockItemRead
    renderWithProviders(
      <DetailsTab
        item={item}
        locale="fr-CH"
        onSaveField={vi.fn().mockRejectedValue(refusal(403, null))}
        onReload={vi.fn()}
        onRecordPurchase={vi.fn()}
      />,
    )

    await editAndSave('VW Golf GTI')

    expect(await screen.findByText(fr.apiRefusal.forbidden)).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).not.toBeInTheDocument()
  })

  // Only a stale If-Match 409 offers the reload: a business rule's 409 is
  // not fixed by reloading (before KAN-473 every 409 offered it).
  it('stock: a business-rule 409 says its reason and offers no reload', async () => {
    installFakeBackend([])
    const NOW = '2026-10-01T09:00:00Z'
    const item = {
      id: 's1', stockNumber: 'ST-1001', vehicleLabel: 'VW Golf GTI', vin: null, vehicleId: null, condition: 'used',
      lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green', odometerKm: null, listPrice: null,
      effectivePrice: null, basePrice: null, bodyStyle: null, exteriorColour: null, firstRegistrationDate: null,
      inStockAt: NOW, leftStockAt: null, isInvoiceable: true, createdAt: NOW, updatedAt: NOW, version: 1,
    } as unknown as StockItemRead
    renderWithProviders(
      <DetailsTab
        item={item}
        locale="fr-CH"
        onSaveField={vi.fn().mockRejectedValue(refusal(409, { reason: 'vin_already_in_stock', stockNumber: 'S-000042' }))}
        onReload={vi.fn()}
        onRecordPurchase={vi.fn()}
      />,
    )

    await editAndSave('VW Golf GTI')

    expect(await screen.findByText(fr.apiRefusal.reasons.vin_already_in_stock.replace('{{stockNumber}}', 'S-000042'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: fr.common.inlineEdit.reload })).not.toBeInTheDocument()
  })

  it("reference data: a refusal without a reason is the field's translated generic", async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /\/reference-data\//,
        handler: () => ({
          items: [
            { valueCode: 'diesel', labelDe: 'Diesel', labelFr: 'Gazole', labelIt: 'Gasolio', labelEn: 'Diesel fuel', active: true, sortOrder: 0, version: 1 },
          ],
          nextCursor: null,
        }),
      },
      {
        method: 'PATCH',
        match: /\/reference-data\/[a-z_]+\/diesel$/,
        handler: () => status(400, { error: { code: 'bad_request', message: ENGLISH, details: null } }),
      },
    ])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    await editAndSave('Gazole')

    expect(await screen.findByText(fr.common.inlineEdit.saveFailed)).toBeInTheDocument()
    expect(screen.queryByText(ENGLISH)).not.toBeInTheDocument()
  })
})
