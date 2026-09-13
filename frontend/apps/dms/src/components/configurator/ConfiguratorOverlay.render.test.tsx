// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useOverlay } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status, type FakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'

// C-C (KAN-41) exit criteria 1/2/3/6. The identification waterfall is
// C-D; here Phase 1 offers browse + "configure manually" + a VIN box.

// jsdom has no layout → TanStack Virtual renders zero rows. Mock it so the
// embedded catalogue grid actually shows its rows.
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

const VARIANT = {
  id: 'v1',
  brandId: 'b1',
  brandDisplayName: 'Volkswagen',
  modelGroupId: 'g1',
  modelGroupName: 'Golf',
  variantName: 'Golf GTI',
  modelYearFrom: 2021,
  modelYearTo: null,
  inProduction: true,
  vehicleKind: 'passenger_car',
  fuelType: 'petrol',
  bodyStyle: 'hatchback',
  drivetrain: 'fwd',
  transmission: 'automatic',
  typeApprovalNumbers: [],
  currentPrice: { amount: '42500.00', year: 2021, isNet: false },
  spec: { ps: 245, displacementCcm: 1984, trimName: 'GTI' },
  updatedAt: '2026-01-01T00:00:00Z',
}

const CATALOGUE_OPTION = {
  id: 'opt-1',
  optionCode: 'NAV',
  description: 'Navigation Pro',
  optionGroup: null,
  price: '1800.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}

const CATALOGUE_COLOUR = {
  colourCode: 'RED',
  description: 'Rosso competizione',
  colourType: 'exterior',
  price: '1100.00',
}

const CATALOGUE_TYRE_SPEC = {
  axle: 'front',
  size: '225/45 R18',
  loadIndex: '95',
  speedRating: 'Y',
  remark: 'nur mit Leichtmetallfelgen',
  season: 'summer',
}

const CATALOGUE_IMAGE = {
  imageKey: 'v1-front.jpg',
  bildTyp: 'S',
  bildArt: 'A',
  sequence: 0,
  imageUrl: 'https://images.autoi.ch/img/v1-front.jpg',
}

interface Ctx {
  backend: FakeBackend
  posted: () => Record<string, unknown>[]
  postedOptions: () => Record<string, unknown>[]
}

function install(over: FakeRoute[] = []): Ctx {
  const posted: Record<string, unknown>[] = []
  const postedOptions: Record<string, unknown>[] = []
  let version = 1
  const configBody = (b: Record<string, unknown>) => ({
    id: 'cfg-1',
    tenantId: 't1',
    source: b.source,
    mode: b.mode,
    catalogueMatchStatus: b.source === 'provider' ? 'matched' : 'unverified',
    matchMethod: b.matchMethod,
    catalogueVariantId: b.catalogueVariantId ?? null,
    catalogueVariantLabel: null,
    vehicleId: null,
    vehicleLabel: null,
    vin: b.vin ?? null,
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: b.firstRegistrationDate ?? null,
    licencePlate: null,
    mileageKm: b.mileageKm ?? null,
    brandDisplayName: b.brandDisplayName ?? 'Volkswagen',
    modelGroupName: b.modelGroupName ?? 'Golf',
    variantName: b.variantName ?? 'Golf GTI',
    vehicleKind: null,
    fuelType: null,
    bodyStyle: null,
    drivetrain: null,
    transmission: null,
    exteriorColour: null,
    interiorColour: null,
    exteriorColourSurcharge: null,
    interiorColourSurcharge: null,
    wheels: null,
    wheelsSurcharge: null,
    spec: (b.spec as Record<string, unknown>) ?? {},
    overriddenFields: [],
    options: [],
    notes: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  })
  const routes: FakeRoute[] = [
    // First match wins — declared before the defaults below so a test's
    // own route (e.g. a simulated failure) actually shadows one.
    ...over,
    { method: 'GET', match: /\/vehicle-mdm\/brands$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
    {
      method: 'GET',
      match: /\/catalogue\/facets$/,
      handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: () => ({ items: [VARIANT], nextCursor: null, total: 1, totalIsEstimate: false, browseAvailable: true }),
    },
    {
      method: 'GET',
      match: /\/catalogue\/variants\/v1\/specification$/,
      handler: () => ({
        hasCatalogueMatch: true,
        hasProviderConnection: true,
        packagesAvailable: true,
        imagesAvailable: true,
        dealerCanUploadImages: false,
        options: [CATALOGUE_OPTION],
        colours: [CATALOGUE_COLOUR],
        tyreSpecs: [CATALOGUE_TYRE_SPEC],
        images: [CATALOGUE_IMAGE],
        optionRelations: [],
      }),
    },
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /\/configurations$/,
      handler: (req) => {
        posted.push(req.body as Record<string, unknown>)
        version = 1
        return { __status: 201, body: { ...configBody(req.body as Record<string, unknown>), version } }
      },
    },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1$/,
      handler: (req) => {
        posted.push(req.body as Record<string, unknown>)
        version += 1
        return { ...configBody(req.body as Record<string, unknown>), version }
      },
    },
    {
      method: 'PATCH',
      match: /\/configurations\/cfg-1\/options$/,
      handler: (req) => {
        postedOptions.push(req.body as Record<string, unknown>)
        version += 1
        return { ...configBody({}), version }
      },
    },
  ]
  const backend = installFakeBackend(routes)
  return { backend, posted: () => posted, postedOptions: () => postedOptions }
}

/** A minimal host: an input whose value must survive, plus a button that
 * pushes the overlay onto the stack (the real hosts in C-F do this). */
function Host({ onCommitted }: { onCommitted: (c: unknown) => void }) {
  const overlay = useOverlay()
  return (
    <div>
      <input aria-label="host draft" />
      <button
        type="button"
        onClick={() =>
          overlay.push({
            key: 'cfg',
            content: (
              <ConfiguratorOverlay
                initialMode="build"
                onCommitted={(c) => onCommitted(c)}
                onClose={() => overlay.pop()}
              />
            ),
          })
        }
      >
        open configurator
      </button>
    </div>
  )
}

async function openOverlay(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'open configurator' }))
  return screen.findByText(i18n.t('configurator.find.title'))
}

describe('ConfiguratorOverlay', () => {
  it('opens on top of a host and keeps the host input mounted (ADR-059)', async () => {
    const user = userEvent.setup()
    install()
    const onCommitted = vi.fn()
    renderWithProviders(<Host onCommitted={onCommitted} />)

    await user.type(screen.getByLabelText('host draft'), 'half-typed offer')
    await openOverlay(user)

    // host input still in the DOM with its value
    expect(screen.getByLabelText<HTMLInputElement>('host draft').value).toBe('half-typed offer')

    // Escape closes the overlay, host survives
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByText(i18n.t('configurator.find.title'))).not.toBeInTheDocument())
    expect(screen.getByLabelText<HTMLInputElement>('host draft').value).toBe('half-typed offer')
  })

  it('manual configuration → a valid configuration with no catalogue variant', async () => {
    const user = userEvent.setup()
    const ctx = install()
    const onCommitted = vi.fn()
    renderWithProviders(<Host onCommitted={onCommitted} />)
    await openOverlay(user)

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.find.manual') }))
    // Phase 2 — specification section
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.posted().length).toBe(1))
    const body = ctx.posted()[0]
    expect(body.source).toBe('manual')
    expect(body.matchMethod).toBe('manual')
    expect(body.catalogueVariantId).toBeNull()
    await waitFor(() => expect(onCommitted).toHaveBeenCalled())
  })

  it('mode drives the record-only prompts (mileage + first registration)', async () => {
    const user = userEvent.setup()
    install()
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    // build (default): no record-only prompts after entering phase 2
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))
    expect(screen.queryByLabelText(i18n.t('configurator.summary.mileage'))).not.toBeInTheDocument()

    // back, switch to record, manual again
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.configure.back') }))
    await user.click(screen.getByRole('radio', { name: i18n.t('configurator.mode.record') }))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))
    expect(screen.getByLabelText(i18n.t('configurator.summary.mileage'))).toBeInTheDocument()
    expect(screen.getByLabelText(i18n.t('configurator.summary.firstRegistration'))).toBeInTheDocument()
  })

  it('picking a catalogue variant carries it into the configuration', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    // the embedded browse grid renders its one row (virtualizer mocked)
    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))

    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.posted().length).toBe(1))
    expect(ctx.posted()[0].source).toBe('provider')
    expect(ctx.posted()[0].catalogueVariantId).toBe('v1')
    expect(ctx.posted()[0].matchMethod).toBe('catalogue_browse')
  })

  it('KAN-43 (C-E) — picking a catalogue colour and typing a wheels description both reach the save body', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByText(i18n.t('configurator.sections.colour')))
    const pickers = await screen.findAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))
    await user.click(pickers[0])
    await user.click(await screen.findByRole('option', { name: 'Rosso competizione' }))
    await user.type(screen.getByLabelText(i18n.t('configurator.wheels.description')), '19" Turini')

    // The catalogue's own tyre dimension is shown as reference material.
    expect(screen.getByTestId('tyre-spec-table')).toBeInTheDocument()
    expect(screen.getByText('nur mit Leichtmetallfelgen')).toBeInTheDocument()

    await user.click(screen.getByText(i18n.t('configurator.sections.images')))
    expect(await screen.findByTestId('images-tab')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.posted().length).toBe(1))
    const body = ctx.posted()[0]
    expect(body.exteriorColour).toBe('Rosso competizione')
    expect(body.exteriorColourSurcharge).toBe('1100.00')
    expect(body.wheels).toBe('19" Turini')
  })

  it('KAN-43 (C-E) — selecting a catalogue option carries it into a separate options save', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByText(i18n.t('configurator.sections.options')))
    const optionRow = await screen.findByText('Navigation Pro')
    await user.click(within(optionRow.closest('[data-testid^="option-row-"]') as HTMLElement).getByRole('checkbox'))

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))

    await waitFor(() => expect(ctx.postedOptions().length).toBe(1))
    const body = ctx.postedOptions()[0] as { options: Record<string, unknown>[] }
    expect(body.options).toHaveLength(1)
    expect(body.options[0]).toMatchObject({
      variantOptionId: 'opt-1',
      optionCode: 'NAV',
      description: 'Navigation Pro',
      price: '1800.00',
      selected: true,
    })
  })

  it('KAN-43 (C-E) — a failed options-save after a successful main save does not desync the version (no 409-loop)', async () => {
    const user = userEvent.setup()
    let optionsAttempts = 0
    const ctx = install([
      {
        method: 'PATCH',
        match: /\/configurations\/cfg-1\/options$/,
        handler: () => {
          optionsAttempts += 1
          if (optionsAttempts === 1) return status(500, { error: { code: 'internal', message: 'boom' } })
          return {
            id: 'cfg-1', tenantId: 't1', source: 'provider', mode: 'build', catalogueMatchStatus: 'matched',
            matchMethod: 'catalogue_browse', catalogueVariantId: 'v1', catalogueVariantLabel: null,
            vehicleId: null, vehicleLabel: null, vin: null, stammnummer: null, typeApprovalNumber: null,
            firstRegistrationDate: null, licencePlate: null, mileageKm: null, brandDisplayName: 'Volkswagen',
            modelGroupName: 'Golf', variantName: 'Golf GTI', vehicleKind: null, fuelType: null, bodyStyle: null,
            drivetrain: null, transmission: null, exteriorColour: null, interiorColour: null,
            exteriorColourSurcharge: null, interiorColourSurcharge: null, wheels: null, wheelsSurcharge: null,
            spec: {}, overriddenFields: [], options: [], notes: null, version: 3,
            createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
          }
        },
      },
    ])
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    // First save: nothing selected yet — establishes cfg-1 at version 1,
    // no options call made at all.
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))
    await waitFor(() => expect(ctx.posted().length).toBe(1))

    await user.click(screen.getByText(i18n.t('configurator.sections.options')))
    const optionRow = await screen.findByText('Navigation Pro')
    await user.click(within(optionRow.closest('[data-testid^="option-row-"]') as HTMLElement).getByRole('checkbox'))

    // Second save: the main PATCH succeeds (server bumps to version 2),
    // then the options PATCH fails (simulated) — a distinct error shows.
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await screen.findByText(i18n.t('configurator.saveOptionsError'))
    await waitFor(() => expect(ctx.posted().length).toBe(2))

    // Third save (retry): if `currentVersion` was correctly advanced to 2
    // after the second save's successful main PATCH, this one sends
    // If-Match: 2 — the exact version the server (this fake's own
    // `version` counter) is actually holding. A stale If-Match: 1 here is
    // the 409-loop symptom the fix prevents.
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(ctx.posted().length).toBe(3))
    const mainPatchCalls = ctx.backend.callsTo(/\/configurations\/cfg-1$/, 'PATCH')
    expect(mainPatchCalls[mainPatchCalls.length - 1].headers.get('If-Match')).toBe('2')
    await waitFor(() => expect(optionsAttempts).toBe(2))
  })

  it('KAN-43 (C-E) — a save after the catalogue specification failed to load never sends an authoritative empty options list', async () => {
    const user = userEvent.setup()
    const ctx = install([
      { method: 'GET', match: /\/catalogue\/variants\/v1\/specification$/, handler: () => status(500) },
    ])
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByText(i18n.t('configurator.sections.options')))
    await screen.findByTestId('options-load-error')

    // First save: no options were ever selectable (the fetch failed), so
    // nothing is sent — correct, matches "nothing to say yet".
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))
    await waitFor(() => expect(ctx.posted().length).toBe(1))
    expect(ctx.postedOptions().length).toBe(0)

    // Second save, spec still never loaded: must still not send anything —
    // sending `{ options: [] }` here would tell the server this
    // configuration has zero options at all, including zero included
    // equipment, purely because the fetch never resolved.
    await user.click(screen.getByRole('button', { name: i18n.t('configurator.save') }))
    await waitFor(() => expect(ctx.posted().length).toBe(2))
    expect(ctx.postedOptions().length).toBe(0)
  })

  it('KAN-43 (C-E) — unchecking then rechecking a selected option preserves an edited equipment-feature list', async () => {
    const user = userEvent.setup()
    const ctx = install([
      {
        method: 'GET',
        match: /\/reference-data\/equipment_feature$/,
        handler: () => ({
          items: [
            { valueCode: 'navigation', labelDe: 'Navigation', labelFr: 'Navigation', labelIt: 'Navigazione', labelEn: 'Navigation' },
            { valueCode: 'air_conditioning', labelDe: 'Klimaanlage', labelFr: 'Climatisation', labelIt: 'Climatizzatore', labelEn: 'Air conditioning' },
          ],
          nextCursor: null,
        }),
      },
    ])
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    const overlayEl = screen.getByRole('dialog')
    await user.click(await within(overlayEl).findByText('Golf GTI'))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByText(i18n.t('configurator.sections.options')))
    const optionRow = (await screen.findByText('Navigation Pro')).closest('[data-testid^="option-row-"]') as HTMLElement

    // Check it, then add an equipment feature the catalogue didn't list.
    await user.click(within(optionRow).getByRole('checkbox'))
    const picker = within(optionRow).getByPlaceholderText(i18n.t('configurator.options.addFeature'))
    await user.click(picker)
    await user.click(await screen.findByRole('option', { name: 'Klimaanlage' }))
    expect(within(optionRow).getByText('Klimaanlage')).toBeInTheDocument()

    // Uncheck, then recheck — the correction must survive.
    await user.click(within(optionRow).getByRole('checkbox'))
    await user.click(within(optionRow).getByRole('checkbox'))
    expect(within(optionRow).getByText('Klimaanlage')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))
    await waitFor(() => expect(ctx.postedOptions().length).toBe(1))
    const body = ctx.postedOptions()[0] as { options: { equipmentFeatures: string[] }[] }
    expect(body.options[0].equipmentFeatures).toEqual(expect.arrayContaining(['air_conditioning']))
  })

  it('KAN-43 (C-E) — a manual configuration shows no catalogue options, and never posts an options save', async () => {
    const user = userEvent.setup()
    const ctx = install()
    renderWithProviders(<Host onCommitted={vi.fn()} />)
    await openOverlay(user)

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.find.manual') }))
    await screen.findByText(i18n.t('configurator.spec.groups.powertrain'))

    await user.click(screen.getByText(i18n.t('configurator.sections.options')))
    expect(screen.getByTestId('options-no-catalogue-variant')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('configurator.saveNew') }))
    await waitFor(() => expect(ctx.posted().length).toBe(1))
    expect(ctx.postedOptions().length).toBe(0)
  })
})
