import { describe, expect, it } from 'vitest'
import {
  defaultColumnLayout,
  reorderColumn,
  resizeColumn,
  resolveColumnLayout,
  toggleColumnVisibility,
  togglePinned,
  type ColumnRegistryEntry,
} from './columnLayout'

const REGISTRY: ColumnRegistryEntry[] = [
  { id: 'number', label: 'Number', defaultVisible: true, locked: true },
  { id: 'name', label: 'Name', defaultVisible: true },
  { id: 'city', label: 'City', defaultVisible: true },
  { id: 'internalNote', label: 'Internal note', defaultVisible: false },
]

describe('defaultColumnLayout', () => {
  it('shows every defaultVisible column and hides the rest, in registry order', () => {
    const layout = defaultColumnLayout(REGISTRY)
    expect(layout.order).toEqual(['number', 'name', 'city', 'internalNote'])
    expect(layout.hidden).toEqual(['internalNote'])
  })
})

describe('resolveColumnLayout', () => {
  it('drops a column the registry no longer knows about', () => {
    const layout = { order: ['number', 'name', 'retiredColumn', 'city'], hidden: [], widths: {}, pinnedLeft: [] }
    const resolved = resolveColumnLayout(REGISTRY, layout)
    expect(resolved.visibleOrder).not.toContain('retiredColumn')
  })

  it('appends a registry column missing from a stale saved order, at the end', () => {
    const layout = { order: ['name', 'number'], hidden: [], widths: {}, pinnedLeft: [] }
    const resolved = resolveColumnLayout(REGISTRY, layout)
    expect(resolved.visibleOrder).toEqual(['name', 'number', 'city', 'internalNote'])
  })

  it('re-asserts a locked column visible even against a stale hidden list', () => {
    const layout = { order: REGISTRY.map((c) => c.id), hidden: ['number'], widths: {}, pinnedLeft: [] }
    const resolved = resolveColumnLayout(REGISTRY, layout)
    expect(resolved.visibleOrder).toContain('number')
    expect(resolved.hiddenIds.has('number')).toBe(false)
  })

  it('drops a stale hidden/pinned id the registry no longer knows about', () => {
    const layout = { order: ['number', 'name'], hidden: ['deletedColumn'], widths: {}, pinnedLeft: ['deletedColumn'] }
    const resolved = resolveColumnLayout(REGISTRY, layout)
    expect(resolved.hiddenIds.has('deletedColumn')).toBe(false)
    expect(resolved.pinnedLeftIds.has('deletedColumn')).toBe(false)
  })
})

describe('toggleColumnVisibility', () => {
  it('hides a visible column and shows a hidden one', () => {
    const layout = defaultColumnLayout(REGISTRY)
    const hidden = toggleColumnVisibility(layout, 'name', false)
    expect(hidden.hidden).toContain('name')
    const shown = toggleColumnVisibility(hidden, 'name', false)
    expect(shown.hidden).not.toContain('name')
  })

  it('refuses to hide a locked column', () => {
    const layout = defaultColumnLayout(REGISTRY)
    const attempt = toggleColumnVisibility(layout, 'number', true)
    expect(attempt).toBe(layout) // unchanged, same reference
  })
})

describe('reorderColumn', () => {
  it('moves a column to sit immediately before another', () => {
    const layout = defaultColumnLayout(REGISTRY)
    const reordered = reorderColumn(layout, 'city', 'number')
    expect(reordered.order).toEqual(['city', 'number', 'name', 'internalNote'])
  })

  it('moves a column to the end when beforeId is null', () => {
    const layout = defaultColumnLayout(REGISTRY)
    const reordered = reorderColumn(layout, 'number', null)
    expect(reordered.order).toEqual(['name', 'city', 'internalNote', 'number'])
  })
})

describe('resizeColumn', () => {
  it('records a width override, clamped to a sane minimum', () => {
    const layout = defaultColumnLayout(REGISTRY)
    expect(resizeColumn(layout, 'name', 240).widths.name).toBe(240)
    expect(resizeColumn(layout, 'name', 10).widths.name).toBe(60)
  })
})

describe('togglePinned', () => {
  it('pins and unpins a column', () => {
    const layout = defaultColumnLayout(REGISTRY)
    const pinned = togglePinned(layout, 'name')
    expect(pinned.pinnedLeft).toContain('name')
    const unpinned = togglePinned(pinned, 'name')
    expect(unpinned.pinnedLeft).not.toContain('name')
  })
})
