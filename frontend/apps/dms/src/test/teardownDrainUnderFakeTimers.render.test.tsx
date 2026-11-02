// @vitest-environment jsdom
import { afterAll, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// KAN-526 review — the shared teardown's drain (src/test/setup.ts) yields on
// the real `setImmediate`, kept when setup loads. A test that leaves fake
// timers on past its own `afterEach` (they fake `setImmediate` too) must not
// hang that teardown: without the kept reference, the shared `afterEach` timed
// out after 10 s here and failed this file.

it('leaves fake timers on into the shared teardown', () => {
  vi.useFakeTimers()
  render(<p>mounted</p>)
  expect(screen.getByText('mounted')).toBeInTheDocument()
})

afterAll(() => {
  vi.useRealTimers()
})
