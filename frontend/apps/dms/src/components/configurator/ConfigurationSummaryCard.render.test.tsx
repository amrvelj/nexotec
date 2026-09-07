// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { ConfigurationSummaryCard } from './ConfigurationSummaryCard'
import type { ConfigurationRead } from '../../api/types'

// C-C (KAN-41) exit criterion 4: "The summary card renders identically in
// every host that shows it — one component, asserted."

function config(over: Partial<ConfigurationRead> = {}): ConfigurationRead {
  return {
    id: 'c1',
    tenantId: 't1',
    source: 'provider',
    mode: 'build',
    catalogueMatchStatus: 'matched',
    matchMethod: 'catalogue_browse',
    catalogueVariantId: 'v1',
    catalogueVariantLabel: 'Volkswagen Golf Golf GTI',
    vehicleId: null,
    vehicleLabel: null,
    vin: null,
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: null,
    licencePlate: null,
    mileageKm: null,
    brandDisplayName: 'Volkswagen',
    modelGroupName: 'Golf',
    variantName: 'Golf GTI',
    vehicleKind: null,
    fuelType: 'petrol',
    bodyStyle: null,
    drivetrain: 'fwd',
    transmission: 'automatic',
    exteriorColour: 'Deep Black',
    interiorColour: null,
    exteriorColourSurcharge: null,
    interiorColourSurcharge: null,
    spec: { ps: 245, displacementCcm: 1984, trimName: 'GTI' },
    overriddenFields: [],
    options: [{ id: 'o1', sequence: 0, variantOptionId: null, optionCode: null, description: 'Sunroof', optionGroup: null, price: '1600.00', isIncluded: false, isPackage: false, selected: true, equipmentFeatures: [] }],
    notes: null,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...over,
  }
}

function renderIn(host: string, c: ConfigurationRead) {
  return render(
    <MantineProvider theme={theme} env="test">
      <div data-testid={host}>
        <ConfigurationSummaryCard configuration={c} />
      </div>
    </MantineProvider>,
  )
}

describe('ConfigurationSummaryCard', () => {
  it('renders the same accessible text in every mock host container', () => {
    const c = config()
    const hosts = ['offer-workspace', 'stock-item', 'valuation', 'vehicle-360']
    const outputs = hosts.map((h) => {
      const { unmount } = renderIn(h, c)
      const card = within(screen.getByTestId(h)).getByTestId('configuration-summary-card')
      const text = card.textContent
      unmount()
      return text
    })
    // one component → identical output in all four hosts
    expect(new Set(outputs).size).toBe(1)
    expect(outputs[0]).toContain('Volkswagen · Golf · Golf GTI · GTI')
    expect(outputs[0]).toContain('Sunroof')
  })

  it('marks an unverified / manual configuration', () => {
    renderIn('h', config({ source: 'manual', catalogueMatchStatus: 'unverified', catalogueVariantId: null }))
    expect(screen.getByText(i18n.t('configurator.badges.unverified'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('configurator.badges.manual'))).toBeInTheDocument()
  })

  it('hides option prices in record mode', () => {
    renderIn('h', config({ mode: 'record' }))
    expect(screen.getByText('Sunroof')).toBeInTheDocument()
    expect(screen.queryByText(/1,600|1'600|1600/)).not.toBeInTheDocument()
  })
})
