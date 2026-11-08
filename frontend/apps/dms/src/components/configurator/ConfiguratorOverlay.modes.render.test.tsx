// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { detailsPresent } from '../../test/catalogueDetails'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { GOLF_CANDIDATE, catalogueVariant, configurationRead, identification } from '../../test/configuratorFixtures'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'

// C-D (KAN-42) and C-F (KAN-10): the host decides which modes are reachable
// (PRD v1.4); what identification found is saved as observed data with the
// method that found it; a reopened configuration re-syncs only the fields
// the advisor ticks (FR-C-16).

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const BASE_ROUTES: FakeRoute[] = [
  { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
  { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
  {
    method: 'GET',
    match: /\/catalogue\/variants$/,
    handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false, browseAvailable: true }),
  },
  { method: 'GET', match: /^\/catalogue\/variants\/v1$/, handler: () => catalogueVariant() },
  {
    method: 'GET',
    match: /\/catalogue\/variants\/v1\/specification$/,
    handler: () => ({
      hasCatalogueMatch: true, hasProviderConnection: true, packagesAvailable: true, imagesAvailable: false, details: detailsPresent,
      dealerCanUploadImages: true, options: [], colours: [], tyreSpecs: [], images: [], optionRelations: [],
    }),
  },
  { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
]

const modeOption = (mode: 'build' | 'record') => i18n.t(`configurator.mode.${mode}`)

describe('ConfiguratorOverlay — the host decides the mode', () => {
  it('a build-only host (offer Path B) shows no mode switch and no record mode at all', async () => {
    installFakeBackend(BASE_ROUTES)
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await screen.findByText(i18n.t('configurator.find.title'))
    expect(screen.queryByLabelText(i18n.t('configurator.mode.label'))).not.toBeInTheDocument()
    expect(screen.queryByText(modeOption('record'))).not.toBeInTheDocument()
  })

  it('a record-only host (valuation, trade-in) shows no build mode', async () => {
    installFakeBackend(BASE_ROUTES)
    renderWithProviders(<ConfiguratorOverlay allowedModes={['record']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await screen.findByText(i18n.t('configurator.find.title'))
    expect(screen.queryByText(modeOption('build'))).not.toBeInTheDocument()
  })

  it('the pipeline host offers both modes', async () => {
    installFakeBackend(BASE_ROUTES)
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build', 'record']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await screen.findByText(i18n.t('configurator.find.title'))
    expect(screen.getByText(modeOption('build'))).toBeInTheDocument()
    expect(screen.getByText(modeOption('record'))).toBeInTheDocument()
  })
})

describe('ConfiguratorOverlay — identification feeds the configuration', () => {
  it('saves the plate, the Stammnummer, the Typenschein and the method that found the car', async () => {
    const user = userEvent.setup()
    const posted: Record<string, unknown>[] = []
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/vehicle-identification$/,
        handler: () =>
          identification({
            kind: 'kontrollschild',
            matchMethod: 'kontrollschild',
            outcome: 'variants',
            observed: {
              vin: null, licencePlate: 'BE123456', stammnummer: '777888999', typeApprovalNumber: '2CD456',
              firstRegistrationDate: '2022-03-01', werkscode: null,
            },
            variants: [GOLF_CANDIDATE],
            notes: ['plate_cache_hit'],
          }),
      },
      {
        method: 'POST',
        match: /^\/configurations$/,
        handler: (req) => {
          posted.push(req.body as Record<string, unknown>)
          return { __status: 201, body: configurationRead({ mode: 'record' }) }
        },
      },
      ...BASE_ROUTES,
    ])
    const onCommitted = vi.fn()
    renderWithProviders(<ConfiguratorOverlay allowedModes={['record']} onCommitted={onCommitted} onClose={vi.fn()} />)

    await user.type(await screen.findByLabelText(i18n.t('configurator.find.idLabel'), { exact: false }), 'BE 123 456')
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.identify.submit') }))
    expect(await screen.findByText(i18n.t('configurator.identify.notes.plate_cache_hit'))).toBeInTheDocument()
    await user.click(screen.getByText('Volkswagen Golf Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1))
    expect(posted[0]).toMatchObject({
      mode: 'record',
      source: 'provider',
      catalogueVariantId: 'v1',
      matchMethod: 'kontrollschild',
      licencePlate: 'BE123456',
      stammnummer: '777888999',
      typeApprovalNumber: '2CD456',
      firstRegistrationDate: '2022-03-01',
      confirmedBestMatchCode: null,
    })
  })
})

describe('ConfiguratorOverlay — re-sync (FR-C-16)', () => {
  it('lists the disagreeing fields, ticks none of them, and applies only the chosen ones', async () => {
    const user = userEvent.setup()
    const applied: { body: unknown; ifMatch: string | null }[] = []
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/configurations\/cfg-1\/resync$/,
        handler: () => [
          { field: 'ps', current: 210, catalogue: 245, overridden: true },
          { field: 'kw', current: 150, catalogue: 160, overridden: false },
        ],
      },
      {
        method: 'PATCH',
        match: /^\/configurations\/cfg-1\/resync$/,
        handler: (req) => {
          applied.push({ body: req.body, ifMatch: req.headers.get('If-Match') })
          return configurationRead({ version: 4, spec: { ps: 210, kw: 160 }, overriddenFields: ['ps'] })
        },
      },
      ...BASE_ROUTES,
    ])
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ version: 3, spec: { ps: 210, kw: 150 }, overriddenFields: ['ps'] })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.resync.open') }))
    const panel = await screen.findByTestId('resync-panel')
    const rows = await within(panel).findAllByRole('checkbox')
    expect(rows).toHaveLength(2)
    rows.forEach((box) => expect(box).not.toBeChecked())
    expect(within(panel).getByText(i18n.t('configurator.resync.overridden'))).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: i18n.t('configurator.resync.apply') })).toBeDisabled()

    await user.click(within(panel).getByRole('checkbox', { name: i18n.t('catalogueBrowse.columns.kw') }))
    await user.click(within(panel).getByRole('button', { name: i18n.t('configurator.resync.apply') }))

    await waitFor(() => expect(applied).toHaveLength(1))
    expect(applied[0]).toEqual({ body: { fields: ['kw'] }, ifMatch: '3' })
  })
})

// KAN-211 — the colour type the advisor named, on a reopened configuration.
describe('ConfiguratorOverlay — colour type on a reopened configuration (KAN-211)', () => {
  const COLOUR_TYPE_ROUTE: FakeRoute = {
    method: 'GET',
    match: /\/reference-data\/colour_type$/,
    handler: () => ({
      items: [
        { id: 'ct-1', listCode: 'colour_type', valueCode: 'metallic', labelDe: 'Metallic', labelFr: 'Métallisé', labelIt: 'Metallizzato', labelEn: 'Metallic', sortOrder: 0, active: true, version: 1 },
        // retired since the configuration stored it
        { id: 'ct-2', listCode: 'colour_type', valueCode: 'chrome', labelDe: 'Chrom', labelFr: 'Chrome', labelIt: 'Cromo', labelEn: 'Chrome', sortOrder: 1, active: false, version: 1 },
      ],
      nextCursor: null,
    }),
  }

  it('shows a retired stored type by its label, disabled, and refuses the save naming the field', async () => {
    const user = userEvent.setup()
    const patched: unknown[] = []
    installFakeBackend([
      COLOUR_TYPE_ROUTE,
      { method: 'PATCH', match: /^\/configurations\/cfg-1$/, handler: (req) => { patched.push(req.body); return configurationRead({ version: 2 }) } },
      ...BASE_ROUTES,
    ])
    renderWithProviders(
      <ConfiguratorOverlay allowedModes={['build']} existing={configurationRead({ exteriorColourType: 'chrome' })} onCommitted={vi.fn()} onClose={vi.fn()} />,
    )

    await user.click(await screen.findByText(i18n.t('configurator.sections.colour')))
    const select = screen.getByTestId('exteriorColourType-select') as HTMLInputElement
    await waitFor(() => expect(select.value).toBe('Chrom'))

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    expect(await screen.findByText(i18n.t('configurator.spec.retiredSaveError', { fields: i18n.t('configurator.colour.exteriorType') }))).toBeInTheDocument()
    expect(patched).toHaveLength(0)
  })

  it('says on the summary why the Energieetikette could not be taken over (energy_label_ambiguous)', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        method: 'GET',
        match: /\/catalogue\/variants\/v1\/specification$/,
        handler: () => ({
          hasCatalogueMatch: true, hasProviderConnection: true, packagesAvailable: true, imagesAvailable: false,
          details: { ...detailsPresent, energyLabel: { state: 'present', loadedAt: '2026-11-08T10:00:00Z', note: 'energy_label_ambiguous' } },
          dealerCanUploadImages: true, options: [], colours: [], tyreSpecs: [], images: [], optionRelations: [],
        }),
      },
      ...BASE_ROUTES,
    ])
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} existing={configurationRead()} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByText(i18n.t('configurator.sections.summary')))
    const notice = await screen.findByTestId('detail-state-energyLabel')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.notes.energyLabelAmbiguous'))
    expect(notice).not.toHaveTextContent('energy_label_ambiguous')
  })

  it('renders no energy-label notice on the summary when the label is simply loaded', async () => {
    const user = userEvent.setup()
    installFakeBackend(BASE_ROUTES)
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} existing={configurationRead()} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByText(i18n.t('configurator.sections.summary')))
    await screen.findByTestId('configuration-summary-card')
    expect(screen.queryByTestId('detail-state-energyLabel')).not.toBeInTheDocument()
  })
})
