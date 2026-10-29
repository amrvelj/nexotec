// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useDebouncedNumberField } from './useDebouncedNumberField'
import { useDebouncedTextField } from './useDebouncedTextField'

// KAN-478 — a refetch sent before a field's save (window focus, another
// field's invalidation) that answers after the save settled carries the
// figure from before the save, and the version from before it. While
// nothing of the field's was pending, both hooks took any server copy that
// differed, so «Neu» went back to «Alt» on screen. Given the row's version,
// a copy is taken only when it is newer than any the field has seen.

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

type Row<T> = { value: T; version: number }

function deferred() {
  let resolve!: () => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<void>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useDebouncedNumberField (KAN-478)', () => {
  function setUp(initial: Row<number>) {
    const save = deferred()
    const onCommit = vi.fn(() => save.promise)
    const hook = renderHook(
      ({ row }: { row: Row<number> }) => useDebouncedNumberField(row.value, 'offer-1', onCommit, 500, row.version),
      { initialProps: { row: initial } },
    )
    const type = (value: number) => act(() => hook.result.current[1](value))
    const settle = async (outcome: 'saved' | 'refused') => {
      await act(async () => {
        if (outcome === 'saved') save.resolve()
        else save.reject(new Error('409'))
        await save.promise.catch(() => undefined)
      })
    }
    return { hook, onCommit, type, settle }
  }

  it("an older copy answering after the field's save settled leaves the saved figure on screen", async () => {
    const { hook, onCommit, type, settle } = setUp({ value: 500, version: 1 })

    type(900)
    act(() => vi.advanceTimersByTime(500))
    expect(onCommit).toHaveBeenCalledWith(900)
    await settle('saved')
    // The save's own answer reaches the cache: 900 at version 2 …
    hook.rerender({ row: { value: 900, version: 2 } })
    // … then the refetch sent before the save answers: 500 at version 1.
    hook.rerender({ row: { value: 500, version: 1 } })

    expect(hook.result.current[0]).toBe(900)
  })

  it('an older copy is not taken even when it arrives before the save\'s own answer', async () => {
    const { hook, type, settle } = setUp({ value: 500, version: 1 })

    type(900)
    act(() => vi.advanceTimersByTime(500))
    await settle('saved')
    hook.rerender({ row: { value: 500, version: 1 } })

    expect(hook.result.current[0]).toBe(900)
  })

  it("a newer copy (a colleague's save) is still taken while nothing is pending (KAN-442)", async () => {
    const { hook, type, settle } = setUp({ value: 500, version: 1 })

    type(900)
    act(() => vi.advanceTimersByTime(500))
    await settle('saved')
    hook.rerender({ row: { value: 900, version: 2 } })
    hook.rerender({ row: { value: 800, version: 3 } })

    expect(hook.result.current[0]).toBe(800)
  })

  it('a refused save shows the server copy the field holds, whatever its version', async () => {
    const { hook, type, settle } = setUp({ value: 500, version: 1 })

    type(900)
    act(() => vi.advanceTimersByTime(500))
    await settle('refused')

    expect(hook.result.current[0]).toBe(500)
  })
})

describe('useDebouncedTextField (KAN-478)', () => {
  function setUp(initial: Row<string>) {
    const save = deferred()
    const onCommit = vi.fn(() => save.promise)
    const hook = renderHook(
      ({ row }: { row: Row<string> }) => useDebouncedTextField(row.value, 'item-1', onCommit, 500, row.version),
      { initialProps: { row: initial } },
    )
    return { hook, onCommit, save }
  }

  it("an older copy answering after the field's save settled leaves the saved text on screen", async () => {
    const { hook, onCommit, save } = setUp({ value: 'Alt', version: 1 })

    act(() => hook.result.current[1]('Neu'))
    act(() => vi.advanceTimersByTime(500))
    expect(onCommit).toHaveBeenCalledWith('Neu')
    await act(async () => {
      save.resolve()
      await save.promise
    })
    hook.rerender({ row: { value: 'Neu', version: 2 } })
    hook.rerender({ row: { value: 'Alt', version: 1 } })

    expect(hook.result.current[0]).toBe('Neu')
  })

  it("a newer copy (a colleague's save) is still taken while nothing is pending (KAN-425)", async () => {
    const { hook, save } = setUp({ value: 'Alt', version: 1 })

    act(() => hook.result.current[1]('Neu'))
    act(() => vi.advanceTimersByTime(500))
    await act(async () => {
      save.resolve()
      await save.promise
    })
    hook.rerender({ row: { value: 'Neu', version: 2 } })
    hook.rerender({ row: { value: 'Kollege', version: 3 } })

    expect(hook.result.current[0]).toBe('Kollege')
  })
})
