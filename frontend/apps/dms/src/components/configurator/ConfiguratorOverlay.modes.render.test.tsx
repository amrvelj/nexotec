// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
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
      hasCatalogueMatch: true, hasProviderConnection: true, packagesAvailable: true, imagesAvailable: false,
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
