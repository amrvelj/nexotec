// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, Modal } from '@mantine/core'
import { renderWithProviders } from './renderWithProviders'
import { installFakeBackend } from './fakeBackend'

// KAN-526 — under `env="test"` Mantine still ran every transition's frames
// and timers, and one fired after its file's jsdom was gone («window is not
// defined», exit 1 with every test green). Under renderWithProviders a
// transition now schedules no animation frame and no timer at all: opening
// and closing a modal leaves nothing behind.

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

describe('render tests run Mantine transitions without frames or timers (KAN-526)', () => {
  let frames: string[]
  let timers: string[]
  beforeEach(() => {
    installFakeBackend([])
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

  it('opens and closes a modal with no transition frame or timer', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ModalHost />)
    await user.click(screen.getByRole('button', { name: 'open' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'close' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect({ frames, timers }).toEqual({ frames: [], timers: [] })
  })
})
