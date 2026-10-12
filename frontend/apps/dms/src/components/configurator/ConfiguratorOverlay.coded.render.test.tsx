// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import {
  CODED_REFERENCE_ROUTES,
  PICKED_CODED_VALUES,
  catalogueVariant,
  configurationRead,
  pickCodedFields,
} from '../../test/configuratorFixtures'
import { CODED_FIELDS, specFieldLabelKey } from '../../configurationOptions'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'

// KAN-96 — a manual configuration's five coded fields (vehicle kind, fuel,
// body, drive, transmission) are picked from the admin's reference lists,
// never typed. A value retired from its list is not offered; one already on
// a saved configuration is shown, flagged, and blocks the save until it is
// replaced (Anto, 2026-10-07: every save uses current list values). The
// fake honours `active` and the API's page cap of 100, as the real one does.

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

function install(over: FakeRoute[] = []): Ctx {
  const saved: Record<string, unknown>[] = []
  const routes: FakeRoute[] = [
    ...over,
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
    {
      method: 'GET',
      match: /\/reference-data\/fuel_type$/,
      handler: (req) => {
        if (Number(req.params.get('limit') ?? 50) > 100) return { __status: 422, body: { error: { code: 'validation_error', message: 'limit' } } }
        const items = req.params.get('active') === 'true' ? FUEL_TYPES.filter((v) => v.active) : FUEL_TYPES
        return { items, nextCursor: null }
      },
    },
    ...CODED_REFERENCE_ROUTES,
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

// Several tests pick all five coded fields: past vitest's 5 s default on a
// loaded machine.
describe('ConfiguratorOverlay — coded fields on a manual configuration (KAN-96)', { timeout: 15_000 }, () => {
  it('offers all five as selection lists of active values only, never free text, and saves the pick', async () => {
    const user = userEvent.setup()
    const ctx = install()
    const onCommitted = vi.fn()
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={onCommitted} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    await screen.findByText(i18n.t('configurator.spec.groups.vehicleType'))

    for (const f of CODED_FIELDS) {
      // A Mantine Select that is not searchable: the input takes no typing.
      const field = screen.getByRole('textbox', { name: new RegExp(`^${i18n.t(f.labelKey)}`) })
      expect(field).toHaveAttribute('readonly')
      // Anto, 2026-10-12 — all five are required, none can be emptied.
      expect(field).toBeRequired()
      expect(within(field.parentElement!).queryByRole('button', { hidden: true })).not.toBeInTheDocument()
    }

    await pickCodedFields()
    await user.click(screen.getByRole('textbox', { name: fuelLabel() }))
    expect(await screen.findByRole('option', { name: 'Diesel' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Benzin' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Wasserstoff' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Diesel' }))

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect(ctx.saved()[0]).toMatchObject({ ...PICKED_CODED_VALUES, source: 'manual', fuelType: 'diesel' })
    await waitFor(() => expect(onCommitted).toHaveBeenCalled())
  })

  it('a manual configuration is not saved until all five are picked (Anto, 2026-10-12)', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await screen.findByRole('textbox', { name: fuelLabel() }))
    await user.click(await screen.findByRole('option', { name: 'Diesel' }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    const missing = CODED_FIELDS.filter((f) => f.key !== 'fuelType').map((f) => i18n.t(f.labelKey)).join(', ')
    expect(await screen.findByText(i18n.t('configurator.spec.codedRequiredSaveError', { fields: missing }))).toBeInTheDocument()
    expect(ctx.saved()).toHaveLength(0)
  })

  it('a retired value on a saved configuration is shown, flagged, and blocks the save until replaced', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({
          source: 'manual', catalogueVariantId: null, catalogueVariantLabel: null, ...PICKED_CODED_VALUES,
          fuelType: 'hydrogen',
        })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const fuel = await screen.findByRole('textbox', { name: fuelLabel() })
    await waitFor(() => expect(fuel).toHaveValue('hydrogen'))
    expect(screen.getByText(i18n.t('configurator.spec.retired'))).toBeInTheDocument()
    // Emptying the field is no way round the guard: a coded field offers no
    // clear button (the API refuses a null, Anto 2026-10-12), so the retired
    // value stays until it is replaced. Mantine renders its clear button
    // aria-hidden, hence `hidden: true`.
    expect(within(fuel.parentElement!).queryByRole('button', { hidden: true })).not.toBeInTheDocument()

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

  it('a saved configuration\'s coded field can be changed but not emptied', async () => {
    const user = userEvent.setup()
    install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ source: 'manual', catalogueVariantId: null, catalogueVariantLabel: null, ...PICKED_CODED_VALUES })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const fuel = await screen.findByRole('textbox', { name: fuelLabel() })
    await waitFor(() => expect(fuel).toHaveValue('Benzin'))
    expect(within(fuel.parentElement!).queryByRole('button', { hidden: true })).not.toBeInTheDocument()

    // Picking the selected option again does not deselect it.
    await user.click(fuel)
    await user.click(await screen.findByRole('option', { name: 'Benzin' }))
    expect(fuel).toHaveValue('Benzin')
  })

  it('a list that cannot be loaded says so on its field instead of offering nothing', async () => {
    const user = userEvent.setup()
    install([{ method: 'GET', match: /\/reference-data\/body_style$/, handler: () => ({ __status: 500, body: {} }) }])
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    expect(await screen.findByText(i18n.t('configurator.spec.listUnavailable'))).toBeInTheDocument()
    expect(screen.getAllByText(i18n.t('configurator.spec.listUnavailable'))).toHaveLength(1)
  })

  it('a provider configuration shows its catalogue codes read-only, labelled, and never sends them back', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ ...PICKED_CODED_VALUES, fuelType: 'hydrogen', bodyStyle: null })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await screen.findByText(i18n.t('configurator.spec.groups.vehicleType'))
    expect(screen.getByText(i18n.t('configurator.spec.codedLocked'))).toBeInTheDocument()
    const fuel = screen.getByRole('textbox', { name: fuelLabel() })
    // retired from its list, still shown by its label
    await waitFor(() => expect(fuel).toHaveValue('Wasserstoff'))
    expect(screen.getByRole('textbox', { name: i18n.t('configurator.spec.fields.drivetrain') })).toHaveValue('Frontantrieb')
    expect(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.columns.bodyStyle') })).toHaveValue('—')
    for (const f of CODED_FIELDS) {
      const field = screen.getByRole('textbox', { name: i18n.t(f.labelKey) })
      expect(field).toHaveAttribute('readonly')
      await user.click(field)
    }
    // read-only: nothing to pick from, nothing to clear
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    for (const f of CODED_FIELDS) expect(ctx.saved()[0]).not.toHaveProperty(f.key)
  })
})

// KAN-247 — a field emptied in the form goes out as null, which the API reads
// as "clear it" (Anto, 2026-10-12, R2); a field the form does not carry is
// left out, which leaves it alone.
describe('ConfiguratorOverlay — clearing a saved field (KAN-247)', () => {
  it('a manual configuration sends null for a cleared field and for a cleared spec field', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['record']}
        existing={configurationRead({
          source: 'manual', mode: 'record', catalogueVariantId: null, catalogueVariantLabel: null, ...PICKED_CODED_VALUES,
          mileageKm: 142000, modelGroupName: '2CV', spec: { ps: 29, displacementCcm: 602 },
        })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await user.clear(await screen.findByLabelText(i18n.t('configurator.summary.mileage')))
    await user.clear(screen.getByRole('textbox', { name: i18n.t('catalogueBrowse.columns.modelGroup') }))
    await user.clear(screen.getByRole('textbox', { name: i18n.t(specFieldLabelKey('ps')) }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    const body = ctx.saved()[0] as { spec: Record<string, unknown> } & Record<string, unknown>
    expect(body.mileageKm).toBeNull()
    expect(body.modelGroupName).toBeNull()
    expect(body.spec).toHaveProperty('ps', null)
    expect(body.spec.displacementCcm).toBe(602)
    // the coded fields go out as they are — never null
    expect(body).toMatchObject(PICKED_CODED_VALUES)
  })

  it('a provider configuration sends null for a cleared field and a cleared spec field', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['record']}
        existing={configurationRead({ mode: 'record', mileageKm: 30000, spec: { ps: 245, kw: 180 } })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    await user.clear(await screen.findByLabelText(i18n.t('configurator.summary.mileage')))
    await user.clear(screen.getByRole('textbox', { name: i18n.t(specFieldLabelKey('ps')) }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))

    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    const body = ctx.saved()[0] as { spec: Record<string, unknown> } & Record<string, unknown>
    expect(body.mileageKm).toBeNull()
    // only the overridden spec field travels, cleared
    expect(body.spec).toEqual({ ps: null })
  })
})

// KAN-239 — the spec block's reference fields (engine cycle, emission
// standard, …) once asked for their lists with `limit=200`, which the API
// answers 422; the catch turned that into an empty dropdown.
describe('ConfiguratorOverlay — spec reference fields (KAN-239)', () => {
  it('offers a list longer than one page, fetched within the API cap', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'GET',
        match: /\/reference-data\/engine_cycle$/,
        handler: (req) => {
          if (Number(req.params.get('limit') ?? 50) > 100) return { __status: 422, body: { error: { code: 'validation_error', message: 'limit' } } }
          return req.params.get('cursor') === 'p2'
            ? { items: [value('four_stroke', 'Viertakt')], nextCursor: null }
            : { items: [value('two_stroke', 'Zweitakt')], nextCursor: 'p2' }
        },
      },
    ])
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    await user.click(await screen.findByRole('textbox', { name: i18n.t(specFieldLabelKey('engineCycle')) }))
    expect(await screen.findByRole('option', { name: 'Zweitakt' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Viertakt' })).toBeInTheDocument()
  })

  it('a spec list that cannot be loaded says so on its field', async () => {
    const user = userEvent.setup()
    install([{ method: 'GET', match: /\/reference-data\/emission_standard$/, handler: () => ({ __status: 500, body: {} }) }])
    renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
    const field = await screen.findByRole('textbox', { name: i18n.t(specFieldLabelKey('emissionStandard')) })
    await waitFor(() => expect(field).toHaveAccessibleDescription(i18n.t('configurator.spec.listUnavailable')))
  })

  it('offers active values only; a stored value retired since stays visible, disabled', async () => {
    const user = userEvent.setup()
    install([
      {
        method: 'GET',
        match: /\/reference-data\/engine_cycle$/,
        handler: () => ({ items: [value('four_stroke', 'Viertakt'), value('two_stroke', 'Zweitakt', false)], nextCursor: null }),
      },
    ])
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ source: 'manual', catalogueVariantId: null, spec: { engineCycle: 'two_stroke' } })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const field = await screen.findByRole('textbox', { name: i18n.t(specFieldLabelKey('engineCycle')) })
    await waitFor(() => expect(field).toHaveValue('Zweitakt'))
    await user.click(field)
    expect(await screen.findByRole('option', { name: 'Viertakt' })).not.toHaveAttribute('data-combobox-disabled')
    expect(screen.getByRole('option', { name: 'Zweitakt' })).toHaveAttribute('data-combobox-disabled')
  })

  // KAN-245 — the API refuses a save carrying a retired spec value, one sent
  // back unchanged included (Anto, 2026-10-12, R5), so the form says so first.
  it('a retired spec value a save would send is flagged and blocks the save until replaced or cleared', async () => {
    const user = userEvent.setup()
    const ctx = install([
      {
        method: 'GET',
        match: /\/reference-data\/engine_cycle$/,
        handler: () => ({ items: [value('four_stroke', 'Viertakt'), value('two_stroke', 'Zweitakt', false)], nextCursor: null }),
      },
    ])
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({
          source: 'manual', catalogueVariantId: null, ...PICKED_CODED_VALUES, spec: { engineCycle: 'two_stroke' },
        })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const label = i18n.t(specFieldLabelKey('engineCycle'))
    const field = await screen.findByRole('textbox', { name: label })
    await waitFor(() => expect(field).toHaveAccessibleDescription(i18n.t('configurator.spec.retired')))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    expect(await screen.findByText(i18n.t('configurator.spec.retiredSaveError', { fields: label }))).toBeInTheDocument()
    expect(ctx.saved()).toHaveLength(0)

    await user.click(field)
    await user.click(await screen.findByRole('option', { name: 'Viertakt' }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect((ctx.saved()[0] as { spec: Record<string, unknown> }).spec.engineCycle).toBe('four_stroke')
  })

  it('a provider configuration\'s retired spec value it does not send blocks nothing', async () => {
    const user = userEvent.setup()
    const ctx = install([
      {
        method: 'GET',
        match: /\/reference-data\/engine_cycle$/,
        handler: () => ({ items: [value('two_stroke', 'Zweitakt', false)], nextCursor: null }),
      },
    ])
    renderWithProviders(
      <ConfiguratorOverlay
        allowedModes={['build']}
        existing={configurationRead({ spec: { engineCycle: 'two_stroke' } })}
        onCommitted={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const field = await screen.findByRole('textbox', { name: i18n.t(specFieldLabelKey('engineCycle')) })
    await waitFor(() => expect(field).toHaveValue('Zweitakt'))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(ctx.saved()).toHaveLength(1))
    expect((ctx.saved()[0] as { spec: Record<string, unknown> }).spec).not.toHaveProperty('engineCycle')
  })
})
