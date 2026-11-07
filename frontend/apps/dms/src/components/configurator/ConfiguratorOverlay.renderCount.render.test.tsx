// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { CODED_REFERENCE_ROUTES, catalogueVariant } from '../../test/configuratorFixtures'
import { CODED_FIELDS, SPEC_FIELD_GROUPS, specFieldLabelKey } from '../../configurationOptions'
import { ConfiguratorOverlay } from './ConfiguratorOverlay'

// KAN-335 (Anto, 2026-11-03, option A) — the draft lives in one state, so
// every keystroke re-renders the overlay. A keystroke must re-render only
// the spec group it lands in, and a coded pick only its own select: never
// the other ~50 inputs. Counted at Mantine's inputs, by label.

const renders = vi.hoisted(() => new Map<string, number>())

vi.mock('@mantine/core', async (importOriginal) => {
  const mantine = await importOriginal<typeof import('@mantine/core')>()
  const { createElement } = await import('react')
  // Mantine's statics (`extend`, `classes`, …) are copied: the kit's theme
  // calls `TextInput.extend` at import.
  const counted = <P extends { label?: unknown }>(Component: React.ComponentType<P>) =>
    Object.assign(function Counted(props: P) {
      if (typeof props.label === 'string') renders.set(props.label, (renders.get(props.label) ?? 0) + 1)
      return createElement(Component, props)
    }, Component)
  return {
    ...mantine,
    Select: counted(mantine.Select as unknown as React.ComponentType<{ label?: unknown }>),
    TextInput: counted(mantine.TextInput as unknown as React.ComponentType<{ label?: unknown }>),
    NumberInput: counted(mantine.NumberInput as unknown as React.ComponentType<{ label?: unknown }>),
  }
})

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

function install() {
  installFakeBackend([
    { method: 'GET', match: /\/catalogue\/model-groups$/, handler: () => ({ items: [] }) },
    { method: 'GET', match: /\/catalogue\/facets$/, handler: () => ({ browseAvailable: true, coded: {}, numeric: {} }) },
    {
      method: 'GET',
      match: /\/catalogue\/variants$/,
      handler: () => ({ items: [catalogueVariant()], nextCursor: null, total: 1, totalIsEstimate: false, browseAvailable: true }),
    },
    ...CODED_REFERENCE_ROUTES,
    { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

const specLabel = (key: string) => i18n.t(specFieldLabelKey(key))
const POWERTRAIN = SPEC_FIELD_GROUPS.find((g) => g.titleKey === 'configurator.spec.groups.powertrain')!
const OTHER_SPEC_LABELS = SPEC_FIELD_GROUPS.filter((g) => g !== POWERTRAIN).flatMap((g) =>
  g.fields.map((f) => specLabel(f.key as string)),
)
const ALL_SPEC_LABELS = SPEC_FIELD_GROUPS.flatMap((g) => g.fields.map((f) => specLabel(f.key as string)))
const CODED_LABELS = CODED_FIELDS.map((f) => i18n.t(f.labelKey))

const rendersOf = (labels: string[]) => Object.fromEntries(labels.map((l) => [l, renders.get(l) ?? 0]))
const none = (labels: string[]) => Object.fromEntries(labels.map((l) => [l, 0]))

async function openManualConfiguration() {
  const user = userEvent.setup()
  install()
  renderWithProviders(<ConfiguratorOverlay allowedModes={['build']} onCommitted={vi.fn()} onClose={vi.fn()} />)
  await user.click(await screen.findByRole('button', { name: i18n.t('configurator.find.manual') }))
  await screen.findByText(i18n.t('configurator.spec.groups.vehicleType'))
  // The coded lists have arrived (their options are what a pick chooses).
  await waitFor(() => expect(screen.getByRole('textbox', { name: new RegExp(`^${CODED_LABELS[0]}`) })).toBeInTheDocument())
  return user
}

describe('ConfiguratorOverlay — a keystroke re-renders only its own group (KAN-335)', () => {
  beforeEach(() => renders.clear())

  it('typing in «PS» re-renders the powertrain group only: no other spec group, no coded select', async () => {
    const user = await openManualConfiguration()
    const ps = screen.getByRole('textbox', { name: specLabel('ps') })
    // Let every list settle, then count from zero.
    await waitFor(() => expect(renders.size).toBeGreaterThan(0))
    await new Promise((r) => setTimeout(r, 50))
    renders.clear()

    await user.type(ps, '150')

    expect(ps).toHaveValue('150')
    expect(renders.get(specLabel('ps')) ?? 0).toBeGreaterThan(0)
    expect(rendersOf(OTHER_SPEC_LABELS)).toEqual(none(OTHER_SPEC_LABELS))
    expect(rendersOf(CODED_LABELS)).toEqual(none(CODED_LABELS))
  })

  it('picking a coded value re-renders that select only: no other coded select, no spec field', async () => {
    await openManualConfiguration()
    const fuel = CODED_FIELDS.find((f) => f.key === 'fuelType')!
    const fuelLabel = i18n.t(fuel.labelKey)
    const field = screen.getByRole('textbox', { name: new RegExp(`^${fuelLabel}`) })
    fireEvent.click(field)
    const option = await waitFor(() => {
      const listbox = document.getElementById(field.getAttribute('aria-controls') ?? '')
      const first = listbox?.querySelector('[role="option"]')
      if (!first) throw new Error(`no option for ${fuelLabel}`)
      return first
    })
    const valueLabel = option.textContent?.trim()
    renders.clear()

    fireEvent.click(option)

    await waitFor(() => expect(field).toHaveValue(valueLabel))
    const otherCoded = CODED_LABELS.filter((l) => l !== fuelLabel)
    expect(rendersOf(otherCoded)).toEqual(none(otherCoded))
    expect(rendersOf(ALL_SPEC_LABELS)).toEqual(none(ALL_SPEC_LABELS))
  })
})
