// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { catalogueVariant, configurationRead } from '../../test/configuratorFixtures'
import { CODED_FIELDS } from '../../configurationOptions'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'

// KAN-96 — a manual configuration's five coded fields (vehicle kind, fuel,
// body, drive, transmission) are picked from the admin's reference lists,
// never typed. A value retired from its list is not offered; one already on
// a saved configuration is shown, flagged, and blocks the save until it is
// replaced (Anto, 2026-10-07: every save uses current list values).

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

const value = (valueCode: string, labelDe: string, active = true) => ({
  id: `rv-${valueCode}`,
  listCode: 'fuel_type',
  valueCode,
  labelDe,
  labelFr: labelDe,
  labelIt: labelDe,
  labelEn: labelDe,
  sortOrder: 0,
  active,
  version: 1,
})

const FUEL_TYPES = [value('petrol', 'Benzin'), value('diesel', 'Diesel'), value('hydrogen', 'Wasserstoff', false)]

interface Ctx {
  saved: () => Record<string, unknown>[]
}

function install(): Ctx {
  const saved: Record<string, unknown>[] = []
  const routes: FakeRoute[] = [
    { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: () => ({ items: [catalogueVariant()], nextCursor: null, total: 1, totalIsEstimate: false, browseAvailable: true }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/variants\/v1\/specification$/,
      handler: () => ({
        hasCatalogueMatch: true, hasProviderConnection: true, packagesAvailable: true, imagesAvailable: false,
        dealerCanUploadImages: true, options: [], colours: [], tyreSpecs: [], images: [], optionRelations: [],
      }),
    },
    { method: 'GET', match: /\/reference-data\/fuel_type$/, handler: () => ({ items: FUEL_TYPES, nextCursor: null }) },
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /\/configurations$/,
      handler: (req) => {
        saved.push(req.body as Record<string, unknown>)
        return { __status: 201, body: configurationRead({ source: 'manual', catalogueVariantId: null }) }
      },
    },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1$/,
      handler: (req) => {
        saved.push(req.body as Record<string, unknown>)
        return configurationRead({ source: 'manual', catalogueVariantId: null, version: 2 })
      },
    },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1\/options$/,
      handler: () => configurationRead({ source: 'manual', catalogueVariantId: null, version: 3 }),
    },
  ]
  installFakeBackend(routes)
  return { saved: () => saved }
}

const fuelLabel = () => i18n.t('catalogueBrowse.columns.fuelType')

describe('ConfiguratorOverlay — coded fields on a manual configuration (KAN-96)', () => {
  it('offers all five as selection lists of active values only, never free text, and saves the pick', async () => {
    const user = userEvent.setup()
    const ctx = install()
    const onCommitted = vi.fn()
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={onCommitted} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    await screen.findByText(i18n.t('configurator.spec.groups.vehicleType'))

    for (const f of CODED_FIELDS) {
      // A Mantine Select that is not searchable: the input takes no typing.
      expect(screen.getByRole('textbox', { name: i18n.t(f.labelKey) })).toHaveAttribute('readonly')
    }

    await user.click(screen.getByRole('textbox', { name: fuelLabel() }))
    expect(await screen.findByRole('option', { name: 'Diesel' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Benzin' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Wasserstoff' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Diesel' }))

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect(ctx.saved()[0]).toMatchObject({
      source: 'manual',
      fuelType: 'diesel',
      vehicleKind: null,
      bodyStyle: null,
      drivetrain: null,
      transmission: null,
    })
    await waitFor(() => expect(onCommitted).toHaveBeenCalled())
  })

  it('a retired value on a saved configuration is shown, flagged, and blocks the save until replaced', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ source: 'manual', catalogueVariantId: null, catalogueVariantLabel: null, fuelType: 'hydrogen' })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const fuel = await screen.findByRole('textbox', { name: fuelLabel() })
    await waitFor(() => expect(fuel).toHaveValue('Wasserstoff'))
    expect(screen.getByText(i18n.t('configurator.spec.retired'))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    expect(
      await screen.findByText(i18n.t('configurator.spec.retiredSaveError', { fields: fuelLabel() })),
    ).toBeInTheDocument()
    expect(ctx.saved()).toHaveLength(0)

    await user.click(fuel)
    await user.click(await screen.findByRole('option', { name: 'Diesel' }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect(ctx.saved()[0].fuelType).toBe('diesel')
  })

  it('a provider configuration shows no coded selection lists and never sends its catalogue codes back', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ fuelType: 'hydrogen', drivetrain: 'fwd' })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))
    expect(screen.queryByText(i18n.t('configurator.spec.groups.vehicleType'))).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    for (const f of CODED_FIELDS) expect(ctx.saved()[0]).not.toHaveProperty(f.key)
  })
})
