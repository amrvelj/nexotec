// @vitest-environment jsdom
import { useState } from 'react'
import { afterAll, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

// KAN-526 — the shared teardown (src/test/setup.ts) lets React's queued
// Scheduler tasks run while `window` still exists. This file ends its only
// test with such a task queued (a state update made outside `act`, as one
// that lands while `findBy*`/`waitFor` waits is), then does what vitest does
// after a file's last test: it removes `window`. Without the drain the
// queued task runs then, reads `window.event` and throws «window is not
// defined» — the error that failed fully green runs.

let setLabelOutsideAct: ((label: string) => void) | null = null

function Probe() {
  const [label, setLabel] = useState('mounted')
  setLabelOutsideAct = (next) => {
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
    const previous = env.IS_REACT_ACT_ENVIRONMENT
    env.IS_REACT_ACT_ENVIRONMENT = false
    try {
      setLabel(next)
    } finally {
      env.IS_REACT_ACT_ENVIRONMENT = previous
    }
  }
  return <p>{label}</p>
}

it('ends with a React update still queued on the Scheduler', () => {
  render(<Probe />)
  expect(screen.getByText('mounted')).toBeInTheDocument()
  setLabelOutsideAct?.('updated outside act')
})

afterAll(async () => {
  const thrown: unknown[] = []
  const record = (error: unknown) => thrown.push(error)
  process.on('uncaughtException', record)
  const env = globalThis as { window?: unknown }
  const jsdomWindow = env.window
  delete env.window
  try {
    for (let i = 0; i < 5; i++) await new Promise<void>((resolve) => setImmediate(resolve))
  } finally {
    env.window = jsdomWindow
    process.off('uncaughtException', record)
  }
  expect(thrown.map(String)).toEqual([])
})
