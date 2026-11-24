// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { useLocation } from 'react-router-dom'
import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend } from '../../../test/fakeBackend'
import { useCreateOfferFromValuation } from './useCreateOfferFromValuation'

// KAN-720: «Offerte aus dieser Bewertung erstellen» once succeeded is done.
// The flaky «a double click starts one run» test was a real defect: a run
// that succeeded reopened the action (and renewed its Idempotency-Key)
// before the navigation to the workspace had rendered, so a second click
// landing in that window — a double click on a loaded machine, or on a fast
// network — created a second offer. Here the trigger stays mounted after the
// navigation, which makes that window as wide as it can be: a click after a
// successful run must start nothing.

const OFFER = { id: 'off-1', offerNumber: 'O-000017', version: 1, contactId: null, tradeInValuationId: null }

function Trigger() {
  const { run } = useCreateOfferFromValuation()
  const location = useLocation()
  return (
    <>
      <button type="button" onClick={() => void run({ id: 'val-1', contactId: null })}>
        go
      </button>
      <p>at {location.pathname}</p>
    </>
  )
}

afterEach(() => cleanup())

describe('useCreateOfferFromValuation — a successful run is final (KAN-720)', () => {
  it('a click after the run succeeded, before the screen has gone, creates no second offer', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'POST', match: /^\/sales\/offers$/, handler: () => OFFER },
      {
        method: 'POST',
        match: /^\/sales\/offers\/off-1\/trade-in\/valuation$/,
        handler: () => ({ ...OFFER, tradeInValuationId: 'val-1', version: 2 }),
      },
    ])
    renderWithProviders(<Trigger />, { route: '/valuations/val-1' })

    await user.click(screen.getByRole('button', { name: 'go' }))
    expect(await screen.findByText('at /sales/offers/off-1')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'go' }))

    expect(backend.callsTo(/^\/sales\/offers$/, 'POST')).toHaveLength(1)
    expect(backend.callsTo(/\/trade-in\/valuation$/, 'POST')).toHaveLength(1)
  })
})
