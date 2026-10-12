// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { CODED_FIELDS } from '../../configurationOptions'
import { installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
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
    wheels: null,
    wheelsSurcharge: null,
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

// KAN-260 — the five coded values are reference codes; the card shows their
// labels in the UI language. Each list holds (code, de, fr, it, en, active).
type Row = [string, string, string, string, string, boolean?]
const LISTS: Record<string, Row[]> = {
  vehicle_kind: [['passenger_car', 'Personenwagen', 'Voiture de tourisme', 'Autovettura', 'Passenger car']],
  fuel_type: [
    ['petrol', 'Benzin', 'Essence', 'Benzina', 'Petrol'],
    // retired since: a stored code still finds its label
    ['lpg', 'Autogas', 'GPL', 'GPL', 'LPG', false],
  ],
  body_style: [
    ['hatchback', 'Schrägheck', 'Berline à hayon', 'Berlina a due volumi', 'Hatchback'],
    // no French label: renders the loud marker under the French UI
    ['targa', 'Targa', '', 'Targa', 'Targa'],
  ],
  drivetrain: [['fwd', 'Frontantrieb', 'Traction avant', 'Trazione anteriore', 'Front-wheel drive']],
  transmission: [['automatic', 'Automat', 'Automatique', 'Automatico', 'Automatic']],
}

function listRoutes(over: FakeRoute[] = []): FakeRoute[] {
  return [
    ...over,
    {
      method: 'GET',
      match: /\/reference-data\/[a-z_]+$/,
      handler: (req) => {
        const listCode = req.pathname.split('/').pop()!
        const rows = LISTS[listCode]
        if (!rows) return status(404, { error: { code: 'not_found', message: listCode } })
        return {
          items: rows
            .filter(([, , , , , active = true]) => req.params.get('active') !== 'true' || active)
            .map(([valueCode, labelDe, labelFr, labelIt, labelEn, active = true], i) => ({
              id: `${listCode}-${valueCode}`, listCode, valueCode, labelDe, labelFr, labelIt, labelEn,
              sortOrder: i, active, version: 1,
            })),
          nextCursor: null,
        }
      },
    },
  ]
}

function renderCard(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider theme={theme} env="test">
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </MantineProvider>,
  )
}

function renderIn(host: string, c: ConfigurationRead) {
  return renderCard(
    <div data-testid={host}>
      <ConfigurationSummaryCard configuration={c} />
    </div>,
  )
}

const coded = { vehicleKind: 'passenger_car', fuelType: 'petrol', bodyStyle: 'hatchback', drivetrain: 'fwd', transmission: 'automatic' }

describe('ConfigurationSummaryCard', () => {
  it('renders the same accessible text in every host, sharing one label request per list', async () => {
    const backend = installFakeBackend(listRoutes())
    const c = config(coded)
    const hosts = ['offer-workspace', 'stock-item', 'valuation', 'vehicle-360']
    renderCard(
      <>
        {hosts.map((h) => (
          <div key={h} data-testid={h}>
            <ConfigurationSummaryCard configuration={c} />
          </div>
        ))}
      </>,
    )
    const card = (h: string) => within(screen.getByTestId(h)).getByTestId('configuration-summary-card')
    await waitFor(() => expect(card(hosts[0]).textContent).toContain('Frontantrieb'))
    const outputs = hosts.map((h) => card(h).textContent)
    // one component → identical output in all four hosts
    expect(new Set(outputs).size).toBe(1)
    expect(outputs[0]).toContain('Volkswagen · Golf · Golf GTI · GTI')
    expect(outputs[0]).toContain('Sunroof')
    // the lookup is shared and cached: four cards, one request per list
    for (const f of CODED_FIELDS) {
      expect(backend.callsTo(new RegExp(`/reference-data/${f.listCode}$`))).toHaveLength(1)
    }
  })

  it('shows all five coded values as labels in the UI language, never their codes (KAN-260)', async () => {
    installFakeBackend(listRoutes())
    renderIn('h', config(coded))

    const card = screen.getByTestId('configuration-summary-card')
    await waitFor(() => expect(card).toHaveTextContent('Personenwagen'))
    for (const [label, value] of [
      [i18n.t('configurator.spec.fields.vehicleKind'), 'Personenwagen'],
      [i18n.t('catalogueBrowse.columns.fuelType'), 'Benzin'],
      [i18n.t('catalogueBrowse.columns.bodyStyle'), 'Schrägheck'],
      [i18n.t('configurator.spec.fields.drivetrain'), 'Frontantrieb'],
      [i18n.t('catalogueBrowse.columns.transmission'), 'Automat'],
    ]) {
      expect(within(card).getByText(label)).toBeInTheDocument()
      expect(within(card).getByText(value)).toBeInTheDocument()
    }
    for (const code of Object.values(coded)) expect(within(card).queryByText(code)).not.toBeInTheDocument()
  })

  it('follows the UI language, labels a retired code, and marks a code without a label', async () => {
    installFakeBackend(listRoutes())
    await i18n.changeLanguage('fr')
    try {
      renderIn('h', config({ ...coded, fuelType: 'lpg', bodyStyle: 'targa', transmission: 'cvt' }))
      const card = screen.getByTestId('configuration-summary-card')
      await waitFor(() => expect(card).toHaveTextContent('Voiture de tourisme'))
      expect(within(card).getByText('Traction avant')).toBeInTheDocument()
      // retired from its list, still labelled
      expect(within(card).getByText('GPL')).toBeInTheDocument()
      // no French label → loud marker, not the bare code
      expect(within(card).getByText('⚠ targa')).toBeInTheDocument()
      // a code its list does not know → loud marker
      expect(within(card).getByText('⚠ cvt')).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })

  it('marks every code of a list that failed to load', async () => {
    installFakeBackend(listRoutes([
      { method: 'GET', match: /\/reference-data\/drivetrain$/, handler: () => status(500, {}) },
    ]))
    renderIn('h', config(coded))
    const card = screen.getByTestId('configuration-summary-card')
    expect(await within(card).findByText('⚠ fwd')).toBeInTheDocument()
    expect(within(card).getByText('Benzin')).toBeInTheDocument()
  })

  it('marks an unverified / manual configuration', () => {
    installFakeBackend(listRoutes())
    renderIn('h', config({ source: 'manual', catalogueMatchStatus: 'unverified', catalogueVariantId: null }))
    expect(screen.getByText(i18n.t('configurator.badges.unverified'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('configurator.badges.manual'))).toBeInTheDocument()
  })

  it('hides option prices in record mode', () => {
    installFakeBackend(listRoutes())
    renderIn('h', config({ mode: 'record' }))
    expect(screen.getByText('Sunroof')).toBeInTheDocument()
    expect(screen.queryByText(/1,600|1'600|1600/)).not.toBeInTheDocument()
  })

  // KAN-160: the option price goes through formatCurrencyChf — the same
  // `CHF 12'500.00` as the printed document, whatever the UI language.
  it("renders an option price as CHF 12'500.00 under the French UI", async () => {
    installFakeBackend(listRoutes())
    await i18n.changeLanguage('fr')
    try {
      renderIn('h', config({ options: [{ ...config().options[0], price: '12500.00' }] }))
      expect(screen.getByText("CHF 12'500.00")).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
