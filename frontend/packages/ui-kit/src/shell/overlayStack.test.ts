import { describe, expect, it } from 'vitest'
import { closeAllEntries, popEntry, pushEntry } from './overlayStack'
import type { OverlayEntry } from './Overlay'

function entry(key: string): OverlayEntry {
  return { key, content: null }
}

describe('pushEntry', () => {
  it('appends to the end, keeping every lower layer', () => {
    const stack = pushEntry([entry('customer')], entry('vehicle'))
    expect(stack.map((e) => e.key)).toEqual(['customer', 'vehicle'])
  })
})

describe('popEntry', () => {
  it('removes exactly the top entry and reports it as closed', () => {
    const stack = [entry('customer'), entry('vehicle')]
    const { next, closed } = popEntry(stack)
    expect(next.map((e) => e.key)).toEqual(['customer'])
    expect(closed?.key).toBe('vehicle')
  })

  it('popping an empty stack closes nothing and stays empty', () => {
    const { next, closed } = popEntry([])
    expect(next).toEqual([])
    expect(closed).toBeUndefined()
  })
})

describe('closeAllEntries', () => {
  it('reports every entry in top-to-bottom order', () => {
    const stack = [entry('customer'), entry('vehicle'), entry('offer')]
    const { closedInOrder } = closeAllEntries(stack)
    expect(closedInOrder.map((e) => e.key)).toEqual(['offer', 'vehicle', 'customer'])
  })

  it('does not mutate the original stack', () => {
    const stack = [entry('customer'), entry('vehicle')]
    closeAllEntries(stack)
    expect(stack.map((e) => e.key)).toEqual(['customer', 'vehicle'])
  })
})
