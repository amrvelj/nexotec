// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, MantineProvider, Modal } from '@mantine/core'
import { testTheme } from './testTheme'

// KAN-533 — the ui-kit's half of KAN-526. Under `env="test"` Mantine still
// runs every transition's two animation frames and its duration timer, and
// one such timer firing after its file's jsdom was gone failed a fully green
// run («window is not defined»). Mounted with `testTheme` (reduced motion
// respected, and this suite's `matchMedia` reporting
// `prefers-reduced-motion: reduce`), opening and closing a modal schedules
// no transition frame and no timer at all.

function ModalHost() {
  const [opened, setOpened] = useState(false)
  return (
    <>
      <Button onClick={() => setOpened(true)}>open</Button>
      <Modal opened={opened} onClose={() => setOpened(false)} title="dialog">
        <Button onClick={() => setOpened(false)}>close</Button>
      </Modal>
    </>
  )
}

describe('ui-kit render tests run Mantine transitions without frames or timers (KAN-533)', () => {
  let frames: string[]
  let timers: string[]
  beforeEach(() => {
    frames = []
    timers = []
    const raf = window.requestAnimationFrame.bind(window)
    const timeout = window.setTimeout.bind(window)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      if (/use-transition/.test(new Error().stack ?? '')) frames.push('frame')
      return raf(cb)
    })
    vi.spyOn(window, 'setTimeout').mockImplementation(((cb: () => void, ms?: number) => {
      if (/use-transition/.test(new Error().stack ?? '')) timers.push(`timer ${ms}`)
      return timeout(cb, ms)
    }) as typeof window.setTimeout)
  })
  afterEach(() => vi.restoreAllMocks())

  it('reports reduced motion from the suite’s matchMedia stand-in', () => {
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true)
    expect(window.matchMedia('(min-width: 600px)').matches).toBe(false)
  })

  it('opens and closes a modal with no transition frame or timer', async () => {
    const user = userEvent.setup()
    render(
      <MantineProvider theme={testTheme} env="test">
        <ModalHost />
      </MantineProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'open' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'close' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect({ frames, timers }).toEqual({ frames: [], timers: [] })
  })
})
