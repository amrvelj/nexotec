// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import type { MappingGapPage, ReferenceValueRead } from '../api/types'
import { MappingGapsQueue } from './MappingGapsQueue'

// KAN-164 — "Last seen" used toLocaleDateString() with no locale, so it
// followed the browser (jsdom: en-US, `3/7/2026`) instead of the FR-13
// dd.MM.yyyy convention every other date in the app goes through formatDate.

// jsdom has no layout, so TanStack Virtual windows down to zero rows — mock
// it to "render every row" (same shim as VehiclesTab.render.test.tsx).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const page = (): MappingGapPage => ({
  items: [
    {
      id: 'gap-1',
      provider: 'auto_i_dat',
      vehicleKind: 'car',
      codeGroup: 'fuel',
      providerCode: 'XZ',
      occurrences: 12500,
      firstSeenAt: '2026-01-02T12:00:00Z',
      lastSeenAt: '2026-03-07T12:00:00Z',
      resolved: false,
      resolvedAt: null,
      resolvedValueCode: null,
    },
  ],
  nextCursor: null,
})

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('MappingGapsQueue — last-seen date follows FR-13 (KAN-164)', () => {
  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`renders dd.MM.yyyy in ${lng}`, async () => {
      await i18n.changeLanguage(lng)
      installFakeBackend([{ match: /^\/vehicle-mdm\/mapping-gaps$/, handler: () => page() }])
      renderWithProviders(<MappingGapsQueue />)
      expect(await screen.findByText('07.03.2026')).toBeInTheDocument()
      expect(screen.queryByText('3/7/2026')).not.toBeInTheDocument()
    })
  }
})

// KAN-77 — the resolve dialog offers only the active values of the gap's own
// reference list (its codeGroup), and a refused resolve shows the server's
// message instead of failing silently with the dialog still open.

const fuelGap = (): MappingGapPage => ({
  items: [{ ...page().items[0], codeGroup: 'fuel_type', providerCode: '17' }],
  nextCursor: null,
})

const value = (valueCode: string, labelDe: string, sortOrder: number): ReferenceValueRead => ({
  id: `fuel_type-${valueCode}`,
  listCode: 'fuel_type',
  valueCode,
  labelDe,
  labelFr: labelDe,
  labelIt: labelDe,
  labelEn: labelDe,
  sortOrder,
  active: true,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  createdBy: null,
  updatedBy: null,
})

function resolveRoutes(resolve: FakeRoute['handler']): FakeRoute[] {
  return [
    { match: /^\/vehicle-mdm\/mapping-gaps$/, handler: () => fuelGap() },
    {
      match: /^\/reference-data\/fuel_type$/,
      handler: () => ({ items: [value('diesel', 'Diesel', 2), value('petrol', 'Benzin', 1)], nextCursor: null }),
    },
    { method: 'POST', match: /^\/vehicle-mdm\/mapping-gaps\/gap-1\/resolve$/, handler: resolve },
  ]
}

async function chooseDiesel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Zuordnen' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByDisplayValue('fuel_type')).toHaveAttribute('readonly')
  await user.click(within(dialog).getByRole('textbox', { name: 'Kanonischer Wert' }))
  const options = await screen.findAllByRole('option')
  expect(options.map((o) => o.textContent)).toEqual(['Benzin (petrol)', 'Diesel (diesel)'])
  await user.click(screen.getByRole('option', { name: 'Diesel (diesel)' }))
  return dialog
}

describe('MappingGapsQueue — resolve dialog (KAN-77)', () => {
  it('sends the gap\'s own list and the chosen value, then closes', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend(resolveRoutes(() => ({ ...fuelGap().items[0], resolved: true })))
    renderWithProviders(<MappingGapsQueue />)

    const dialog = await chooseDiesel(user)
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const [call] = backend.callsTo(/\/resolve$/, 'POST')
    expect(call.body).toEqual({ canonicalListCode: 'fuel_type', canonicalValueCode: 'diesel' })
  })

  it('shows the server\'s message when the resolve is refused, and keeps the dialog open', async () => {
    const user = userEvent.setup()
    installFakeBackend(
      resolveRoutes(() =>
        status(409, {
          error: { code: 'conflict', message: "Provider code '17' is already mapped to fuel_type/petrol.", details: null },
        }),
      ),
    )
    renderWithProviders(<MappingGapsQueue />)

    const dialog = await chooseDiesel(user)
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      "Provider code '17' is already mapped to fuel_type/petrol.",
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('says so when the gap\'s code group has no reference list, instead of offering values', async () => {
    const user = userEvent.setup()
    installFakeBackend([
      {
        match: /^\/vehicle-mdm\/mapping-gaps$/,
        handler: () => ({ items: [{ ...page().items[0], codeGroup: '011', providerCode: '7' }], nextCursor: null }),
      },
      {
        match: /^\/reference-data\/011$/,
        handler: () => status(404, { error: { code: 'not_found', message: "Reference list '011' was not found.", details: null } }),
      },
    ])
    renderWithProviders(<MappingGapsQueue />)

    await user.click(await screen.findByRole('button', { name: 'Zuordnen' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/Die Codegruppe «011» hat keine Referenzliste/)).toBeInTheDocument()
    expect(within(dialog).getByRole('textbox', { name: 'Kanonischer Wert' })).toBeDisabled()
  })
})

describe('MappingGapsQueue — one Idempotency-Key per resolution (KAN-266)', () => {
  // The first resolve fails; every later one succeeds.
  function installFailingFirstResolve() {
    let attempts = 0
    const backend = installFakeBackend(
      resolveRoutes(() => {
        attempts += 1
        if (attempts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
        return { ...fuelGap().items[0], resolved: true }
      }),
    )
    return () => backend.callsTo(/\/resolve$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
  }

  it('a resolve retried after a failure carries the same key', async () => {
    const user = userEvent.setup()
    const keys = installFailingFirstResolve()
    renderWithProviders(<MappingGapsQueue />)

    const dialog = await chooseDiesel(user)
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Try again.')
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(keys()).toHaveLength(2)
    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBe(keys()[0])
  })

  it('another value chosen after a failure is another request and gets a new key', async () => {
    const user = userEvent.setup()
    const keys = installFailingFirstResolve()
    renderWithProviders(<MappingGapsQueue />)

    const dialog = await chooseDiesel(user)
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Try again.')
    await user.click(within(dialog).getByRole('textbox', { name: 'Kanonischer Wert' }))
    await user.click(await screen.findByRole('option', { name: 'Benzin (petrol)' }))
    await user.click(within(dialog).getByRole('button', { name: 'Zuordnen' }))
    await waitFor(() => expect(keys()).toHaveLength(2))

    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})
