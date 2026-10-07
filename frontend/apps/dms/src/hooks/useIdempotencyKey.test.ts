// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useIdempotencyKey } from './useIdempotencyKey'

describe('useIdempotencyKey (KAN-119)', () => {
  it('keeps one key across retries and re-renders, and mints a fresh one once renewed', () => {
    const { result, rerender } = renderHook(() => useIdempotencyKey())

    const first = result.current.headers()['Idempotency-Key']
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
    rerender()
    expect(result.current.headers()['Idempotency-Key']).toBe(first)

    act(() => result.current.renew())
    const next = result.current.headers()['Idempotency-Key']
    expect(next).not.toBe(first)
    expect(result.current.headers()['Idempotency-Key']).toBe(next)
  })
})
