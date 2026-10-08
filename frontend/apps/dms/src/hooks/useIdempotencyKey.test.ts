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

  it('with a request fingerprint: a retry keeps the key, a different request gets a new one (KAN-266)', () => {
    const { result } = renderHook(() => useIdempotencyKey())
    const key = (request: unknown) => result.current.headers(request)['Idempotency-Key']

    const first = key(['/customers/c1/phones', { phoneE164: '+41791110000' }])
    expect(key(['/customers/c1/phones', { phoneE164: '+41791110000' }])).toBe(first)

    const corrected = key(['/customers/c1/phones', { phoneE164: '+41791119999' }])
    expect(corrected).not.toBe(first)

    const otherCustomer = key(['/customers/c2/phones', { phoneE164: '+41791119999' }])
    expect(otherCustomer).not.toBe(corrected)

    act(() => result.current.renew())
    expect(key(['/customers/c2/phones', { phoneE164: '+41791119999' }])).not.toBe(otherCustomer)
  })
})

