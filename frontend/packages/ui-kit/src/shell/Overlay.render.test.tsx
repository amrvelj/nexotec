// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OverlayProvider, useOverlay } from './Overlay'

// § ADR-059 — the component-level half of the guarantee the app-level
// OfferWorkspace overlay test also covers: "Every layer below the top
// stays MOUNTED (`display: none`, not unmounted) while covered, so its own
// state survives however many screens get opened on top of it." The pure
// stack ordering already has overlayStack.test.ts; this proves the React
// layer keeps covered content in the DOM rather than tearing it down.

function Base() {
  const overlay = useOverlay()
  return (
    <div>
      <input aria-label="base field" />
      <button type="button" onClick={() => overlay.push({ key: 'l1', content: <LayerOne /> })}>
        open layer one
      </button>
    </div>
  )
}

function LayerOne() {
  const overlay = useOverlay()
  return (
    <div>
      <input aria-label="layer one field" />
      <button type="button" onClick={() => overlay.push({ key: 'l2', content: <LayerTwo /> })}>
        open layer two
      </button>
    </div>
  )
}

function LayerTwo() {
  const overlay = useOverlay()
  return (
    <button type="button" onClick={() => overlay.pop()}>
      close layer two
    </button>
  )
}

describe('OverlayProvider keeps covered layers mounted (ADR-059)', () => {
  it('a layer that gets covered keeps its DOM node and its unsaved input value', async () => {
    const user = userEvent.setup()
    render(
      <OverlayProvider>
        <Base />
      </OverlayProvider>,
    )

    await user.type(screen.getByLabelText('base field'), 'draft on the host')
    await user.click(screen.getByRole('button', { name: 'open layer one' }))

    const layerOneField = screen.getByLabelText<HTMLInputElement>('layer one field')
    await user.type(layerOneField, 'half-typed correction')

    // Cover layer one with layer two.
    await user.click(screen.getByRole('button', { name: 'open layer two' }))

    // Covered — not on screen, but still in the tree with its value intact.
    const coveredLayerOne = screen.getByLabelText<HTMLInputElement>('layer one field')
    expect(coveredLayerOne).toBeInTheDocument()
    expect(coveredLayerOne.value).toBe('half-typed correction')
    expect(coveredLayerOne.closest('[role="dialog"]')).toHaveStyle({ display: 'none' })

    // The host screen underneath every overlay is likewise untouched.
    expect(screen.getByLabelText<HTMLInputElement>('base field').value).toBe('draft on the host')

    // Uncover — layer one is interactive again, still holding its value.
    await user.click(screen.getByRole('button', { name: 'close layer two' }))
    const uncovered = screen.getByLabelText<HTMLInputElement>('layer one field')
    expect(uncovered.value).toBe('half-typed correction')
    expect(uncovered.closest('[role="dialog"]')).toHaveStyle({ display: 'block' })
  })
})
