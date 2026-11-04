// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, configurationRead } from '../../test/configuratorFixtures'
import { DetailsTab } from './DetailsTab'
import { HostConfigurationCard } from '../configurator/HostConfigurationCard'
import { SpecificationTab } from '../vehicle-detail/SpecificationTab'
import type { StockItemRead } from '../../api/types'

// KAN-459 — a salesperson on a stock vehicle could not answer «what is the
// towing capacity?»: the stock detail showed only a summary (PS, ccm, first
// registration, mileage, colour). PRD-Stock FR-I-07 block 2 is the full
// technical data, read-only, absent values «Nicht gesetzt» in italic. The
// same section, through the same card, shows on Vehicle 360 and the offer.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const NOW = '2026-10-01T09:00:00Z'
const ITEM: StockItemRead = {
  id: 's1', stockNumber: 'S-000002', vehicleLabel: 'VW Golf 8 GTI', vin: 'WVWZZZ1KZ00000001', vehicleId: null,
  condition: 'used', lifecycleStatus: 'in_stock', reservationState: 'none', ageingBucket: 'green',
  odometerKm: 12500, listPrice: '42500.00', effectivePrice: null, basePrice: null, bodyStyle: null,
  exteriorColour: null, firstRegistrationDate: '2021-04-01', inStockAt: NOW, leftStockAt: null, isInvoiceable: true,
  landedCost: null, locationId: null, notionalInputTaxAmount: null, notionalInputTaxApplicable: null,
  notionalInputTaxOverridden: false, notionalInputTaxRate: null, orderDate: null, expectedDelivery: null,
  pipelineRef: null, purchaseDate: null, purchaseInvoiceRef: null, purchasePrice: null, supplierIsVatRegistered: null,
  supplierName: null, valuationRefAmount: null, valuationRefId: null, valuationRefSource: null,
  valuationRefValuedAt: null, createdAt: NOW, updatedAt: NOW, version: 1,
  configurationId: 'cfg-1',
}

const CONFIGURATION = configurationRead({
  exteriorColour: 'Kings Red Metallic',
  spec: {
    ps: 245,
    kw: 180,
    displacementCcm: 1984,
    towingCapacityKg: 1600,
    weightEmptyKg: null,
    consumptionMixed: '6.5',
    consumptionNorm: 'wltp',
    emissionStandard: 'euro_6d',
    co2Gkm: 148,
    modelYear: 2021,
    productionFrom: 201903,
    basePrice: '48900.00',
    basePriceYear: 2021,
  },
})

// The spec block's own reference lists (KAN-260: labels, never codes).
const SPEC_REFERENCE_ROUTES: FakeRoute[] = [
  {
    method: 'GET',
    match: /\/reference-data\/[a-z_]+$/,
    handler: (req) => {
      const listCode = req.pathname.split('/').pop()!
      const rows: Record<string, [string, string, string, string, string]> = {
        emission_standard: ['euro_6d', 'Euro 6d', 'Euro 6d', 'Euro 6d', 'Euro 6d'],
        consumption_norm: ['wltp', 'WLTP', 'WLTP', 'WLTP', 'WLTP'],
      }
      const row = rows[listCode]
      return {
        items: row
          ? [{ id: `rv-${row[0]}`, listCode, valueCode: row[0], labelDe: row[1], labelFr: row[2], labelIt: row[3], labelEn: row[4], sortOrder: 0, active: true, version: 1 }]
          : [],
        nextCursor: null,
      }
    },
  },
]

function backend() {
  return installFakeBackend([
    ...CODED_REFERENCE_ROUTES,
    ...SPEC_REFERENCE_ROUTES,
    { method: 'GET', match: /^\/configurations\/cfg-1$/, handler: () => CONFIGURATION },
    { method: 'GET', match: /^\/vehicle-mdm\/veh-1\/configuration$/, handler: () => CONFIGURATION },
  ])
}

/** The spec-grid cell whose label is exactly `label`. */
function cell(scope: HTMLElement, label: string): HTMLElement {
  return within(scope).getByText(label, { selector: 'div' }).parentElement as HTMLElement
}

describe('Stock detail — technical data (KAN-459, PRD-Stock FR-I-07 block 2)', () => {
  it('shows the towing capacity «1\'600 kg» beside its label, and every stored figure formatted', async () => {
    backend()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    const block = await screen.findByTestId('configuration-technical-data')
    expect(within(block).getByText(i18n.t('configurator.summary.technicalData'))).toBeInTheDocument()
    // The label drops its "(kg)": the value carries the unit.
    expect(cell(block, 'Anhängelast')).toHaveTextContent("Anhängelast1'600 kg")
    expect(cell(block, 'Hubraum')).toHaveTextContent("1'984 cm³")
    expect(cell(block, i18n.t('configurator.spec.fields.consumptionMixed'))).toHaveTextContent('6.5 l/100 km')
    expect(cell(block, 'CO₂')).toHaveTextContent('148 g/km')
    // A year is never grouped; a production month reads MM.YYYY.
    expect(cell(block, i18n.t('catalogueBrowse.columns.modelYear'))).toHaveTextContent(/2021$/)
    expect(cell(block, i18n.t('catalogueBrowse.columns.productionFrom'))).toHaveTextContent('03.2019')
    // Reference-coded values show their label, never the code.
    expect(await within(block).findByText('Euro 6d')).toBeInTheDocument()
    expect(within(block).getByText('WLTP')).toBeInTheDocument()
    expect(within(block).queryByText('euro_6d')).not.toBeInTheDocument()
    // Colours, as delivered.
    expect(cell(block, i18n.t('configurator.colour.exterior'))).toHaveTextContent('Kings Red Metallic')
  })

  it('renders an absent value as italic «Nicht gesetzt», never zero or blank', async () => {
    backend()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    const block = await screen.findByTestId('configuration-technical-data')
    const empty = within(cell(block, 'Leergewicht')).getByText('Nicht gesetzt')
    expect(empty).toHaveStyle({ fontStyle: 'italic' })
    expect(within(cell(block, i18n.t('configurator.colour.interior'))).getByText('Nicht gesetzt')).toBeInTheDocument()
  })

  it('keeps the commercial price group out of the technical block', async () => {
    backend()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    const block = await screen.findByTestId('configuration-technical-data')
    expect(within(block).queryByText(i18n.t('configurator.spec.groups.price'))).not.toBeInTheDocument()
    expect(within(block).queryByText(i18n.t('catalogueBrowse.columns.basePrice'))).not.toBeInTheDocument()
    expect(within(block).queryByText("CHF 48'900.00")).not.toBeInTheDocument()
  })

  it('puts the technical data straight after the hero band, before the commercial card (block 2)', async () => {
    backend()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    const block = await screen.findByTestId('configuration-technical-data')
    const commercial = screen.getByText(i18n.t('stockDetail.commercial.title'))
    expect(block.compareDocumentPosition(commercial) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('follows the UI language', async () => {
    await i18n.changeLanguage('fr')
    backend()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="fr-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    const block = await screen.findByTestId('configuration-technical-data')
    expect(within(block).getByText('Données techniques')).toBeInTheDocument()
    expect(within(cell(block, 'Poids à vide')).getByText('Non défini')).toBeInTheDocument()
  })
})

describe('One technical section in every host (KAN-459 exit criterion 2)', () => {
  it('stock detail, Vehicle 360 and the offer card render the identical section', async () => {
    backend()
    renderWithProviders(
      <>
        <div data-testid="stock">
          <DetailsTab item={ITEM} locale="de-CH" onSaveField={vi.fn()} onReload={vi.fn()} onRecordPurchase={vi.fn()} />
        </div>
        <div data-testid="vehicle-360">
          <SpecificationTab vehicleId="veh-1" />
        </div>
        <div data-testid="offer">
          <HostConfigurationCard configurationId="cfg-1" onOpenConfigurator={vi.fn()} />
        </div>
      </>,
    )

    const section = async (host: string) => {
      const scope = screen.getByTestId(host)
      const block = await within(scope).findByTestId('configuration-technical-data')
      await within(block).findByText('Euro 6d')
      return block
    }
    const stock = await section('stock')
    const v360 = await section('vehicle-360')
    const offer = await section('offer')
    // Same fields, same values, same order; only the column count differs.
    expect(v360.textContent).toBe(stock.textContent)
    expect(offer.textContent).toBe(stock.textContent)
  })
})

// KAN-556 (ADR-117 (2)) — a configured car is named by its configuration and
// follows it: «Bezeichnung» is shown, never edited here (the server refuses
// another name, `vehicle_label_follows_configuration`). A car without a
// configuration keeps its inline editor.
describe('Stock detail — a configured car\'s name follows its configuration (KAN-556)', () => {
  it('shows the name read-only, saying why, for a configured car', async () => {
    backend()
    const onSaveField = vi.fn()
    renderWithProviders(
      <DetailsTab item={ITEM} locale="de-CH" onSaveField={onSaveField} onReload={vi.fn()} onRecordPurchase={vi.fn()} />,
    )

    await screen.findByTestId('configuration-technical-data')
    const name = screen.getByText('VW Golf 8 GTI', { selector: 'span' })
    expect(name).toHaveAttribute('title', i18n.t('stockDetail.identity.vehicleLabelFollowsConfiguration'))
    name.click()
    expect(screen.queryByDisplayValue('VW Golf 8 GTI')).not.toBeInTheDocument()
    expect(onSaveField).not.toHaveBeenCalled()
  })

  it('keeps the inline editor for a car without a configuration', async () => {
    backend()
    renderWithProviders(
      <DetailsTab
        item={{ ...ITEM, configurationId: null }}
        locale="de-CH"
        onSaveField={vi.fn()}
        onReload={vi.fn()}
        onRecordPurchase={vi.fn()}
      />,
    )

    const name = await screen.findByText('VW Golf 8 GTI')
    expect(name).not.toHaveAttribute('title', i18n.t('stockDetail.identity.vehicleLabelFollowsConfiguration'))
    name.click()
    expect(await screen.findByDisplayValue('VW Golf 8 GTI')).toBeInTheDocument()
  })
})
