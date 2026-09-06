// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useOverlay } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeBackend, type FakeRoute } from '../../test/fakeBackend'
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

interface Ctx {
  backend: FakeBackend
  posted: () => Record<string, unknown>[]
}

function install(over: FakeRoute[] = []): Ctx {
  const posted: Record<string, unknown>[] = []
  const routes: FakeRoute[] = [
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
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    {
      method: 'POST',
      match: /\/configurations$/,
      handler: (req) => {
        posted.push(req.body as Record<string, unknown>)
        const b = req.body as Record<string, unknown>
        return {
          __status: 201,
          body: {
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
            spec: (b.spec as Record<string, unknown>) ?? {},
            overriddenFields: [],
            options: [],
            notes: null,
            version: 1,
            createdAt: '2026-01-01T00:00:00Z',
            updatedAt: '2026-01-01T00:00:00Z',
          },
        }
      },
    },
    ...over,
  ]
  const backend = installFakeBackend(routes)
  return { backend, posted: () => posted }
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
})
