// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { testTheme } from '../../test/testTheme'
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
    exteriorColourType: null,
    interiorColourType: null,
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
  // KAN-211: the hand-named colour types.
  colour_type: [['metallic', 'Metallic', 'Métallisé', 'Metallizzato', 'Metallic']],
  interior_colour_type: [['leather', 'Leder', 'Cuir', 'Pelle', 'Leather']],
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
    <MantineProvider theme={testTheme} env="test">
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

// KAN-333 — the first registration date is date-only: it showed raw ISO;
// now dd.MM.yyyy, the recorded day even west of Greenwich.
describe('ConfigurationSummaryCard first registration date (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders dd.MM.yyyy as the recorded day', async () => {
    installFakeBackend(listRoutes())
    renderIn('h', config({ ...coded, firstRegistrationDate: '1975-06-01' }))
    const card = screen.getByTestId('configuration-summary-card')
    await waitFor(() => expect(card).toHaveTextContent('01.06.1975'))
    expect(card).not.toHaveTextContent('1975-06-01')
    expect(card).not.toHaveTextContent('31.05.1975')
  })

  // KAN-547 / KAN-479 (ADR-042): the Energieetikette names the year it applied
  // to, in the technical block's «Verbrauch & Energie»; absent is «Nicht gesetzt».
  it('shows the Energieetikette with its year in the technical block, and «not set» without one', async () => {
    installFakeBackend(listRoutes())
    const { unmount } = renderCard(
      <ConfigurationSummaryCard
        configuration={config({ ...coded, energyRating: { category: 'b', year: 2021, source: 'bfe' } })}
        technicalDataColumns={2}
      />,
    )
    const block = screen.getByTestId('configuration-technical-data')
    await waitFor(() => expect(block).toHaveTextContent(i18n.t('configurator.spec.fields.energyLabel')))
    expect(block).toHaveTextContent('B (2021)')
    unmount()

    renderCard(<ConfigurationSummaryCard configuration={config({ ...coded, energyRating: null })} technicalDataColumns={2} />)
    const label = within(screen.getByTestId('configuration-technical-data')).getByText(i18n.t('configurator.spec.fields.energyLabel'))
    expect(label.parentElement).toHaveTextContent(i18n.t('common.notSet'))
  })
})

// KAN-211 (Anto, 2026-11-08): the colour name as delivered, the type the
// advisor named beside it — «Deep Black · Metallic» — as a label in the UI
// language, in the compact summary and in the technical block's colours group.
describe('ConfigurationSummaryCard — colour type (KAN-211)', () => {
  it('shows the colour with its type label, in the UI language', async () => {
    installFakeBackend(listRoutes())
    renderCard(
      <ConfigurationSummaryCard
        configuration={config({ ...coded, exteriorColourType: 'metallic', interiorColour: 'Titanschwarz', interiorColourType: 'leather' })}
        technicalDataColumns={2}
      />,
    )
    const block = screen.getByTestId('configuration-technical-data')
    await waitFor(() => expect(block).toHaveTextContent('Deep Black · Metallic'))
    expect(block).toHaveTextContent('Titanschwarz · Leder')
    expect(block).not.toHaveTextContent('metallic')
  })

  it('shows the type alone when the colour has no name, and the name alone without a type', async () => {
    installFakeBackend(listRoutes())
    renderIn('h', config({ ...coded, exteriorColour: null, exteriorColourType: 'metallic' }))
    const card = screen.getByTestId('configuration-summary-card')
    await waitFor(() => expect(card).toHaveTextContent('Metallic'))
    const colour = within(card).getByText(i18n.t('configurator.summary.colour')).parentElement as HTMLElement
    expect(colour).toHaveTextContent(/Metallic$/)
    expect(colour).not.toHaveTextContent('·')
  })

  it('reads «Deep Black» alone without a type, asking for no list', async () => {
    const backend = installFakeBackend(listRoutes())
    renderIn('h', config(coded))
    await waitFor(() => expect(screen.getByTestId('configuration-summary-card')).toHaveTextContent('Deep Black'))
    expect(backend.callsTo(/reference-data\/colour_type/).length).toBe(0)
  })

  it('follows the UI language for the type label', async () => {
    await i18n.changeLanguage('fr')
    try {
      installFakeBackend(listRoutes())
      renderIn('h', config({ ...coded, exteriorColourType: 'metallic' }))
      await waitFor(() => expect(screen.getByTestId('configuration-summary-card')).toHaveTextContent('Deep Black · Métallisé'))
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})

// KAN-595 / KAN-596 / KAN-597 — the stock car's technical data (this card,
// through HostConfigurationCard): option names in the UI language as the
// provider delivered them, the approved wheels with their rims, and each
// gearbox's Energieetikette as a PDF link beside the class.
describe('ConfigurationSummaryCard — provider texts, wheels, label links', () => {
  it('names an option in the UI language, the delivered text where none was sent (KAN-595)', async () => {
    installFakeBackend(listRoutes())
    const options = [
      { ...config().options[0], id: 'o1', description: 'Wärmepumpe', descriptions: { de: 'Wärmepumpe', fr: 'Pompe à chaleur', it: null, en: null } },
      { ...config().options[0], id: 'o2', description: 'Anhängerkupplung', descriptions: { de: null, fr: null, it: null, en: null } },
    ]
    await i18n.changeLanguage('fr')
    try {
      renderIn('h', config({ options }))
      expect(screen.getByText('Pompe à chaleur')).toBeInTheDocument()
      expect(screen.getByText('Anhängerkupplung')).toBeInTheDocument()
      expect(screen.queryByText('Wärmepumpe')).not.toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })

  it('lists the approved wheels with their rims in the colours group (KAN-596)', async () => {
    installFakeBackend(listRoutes())
    renderCard(
      <ConfigurationSummaryCard
        configuration={config({
          ...coded,
          catalogueTyreSpecs: [
            {
              axle: 'both', size: '205/60 R16', loadIndex: '92', speedRating: 'V', remark: null, season: 'summer',
              rims: '6.5Jx16', rimWidth: '6.5', rimSize: 16, rimOffset: 46, isStandard: true,
            },
          ],
        })}
        technicalDataColumns={2}
      />,
    )
    const block = screen.getByTestId('configuration-technical-data')
    await waitFor(() => expect(block).toHaveTextContent(i18n.t('configurator.wheels.catalogue')))
    expect(block).toHaveTextContent(
      `205/60 R16 · 6.5Jx16 ET46 · ${i18n.t('configurator.wheels.standard')} · ${i18n.t('configurator.wheels.axlePosition.both')} · ${i18n.t('configurator.wheels.seasonLabel.summer')}`,
    )
  })

  it('links each gearbox label as PDF beside the class, and nothing without a link (KAN-597)', async () => {
    installFakeBackend(listRoutes())
    renderCard(
      <ConfigurationSummaryCard
        configuration={config({
          ...coded,
          energyRating: null,
          energyLabels: [
            { year: 2025, gearbox: 'Automat', category: 'c', labelUrl: 'https://energielabel.example.test/a.pdf' },
            { year: 2025, gearbox: 'Manuell', category: 'b', labelUrl: null },
          ],
        })}
        technicalDataColumns={2}
      />,
    )
    const block = screen.getByTestId('configuration-technical-data')
    const link = await within(block).findByRole('link', {
      name: i18n.t('configurator.spec.energyLabelPdfGearbox', { gearbox: 'Automat', category: 'C' }),
    })
    expect(link).toHaveAttribute('href', 'https://energielabel.example.test/a.pdf')
    expect(link).toHaveAttribute('target', '_blank')
    expect(within(block).getAllByRole('link')).toHaveLength(1)
  })
})
