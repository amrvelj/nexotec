// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { useLocation } from 'react-router-dom'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { VehicleSearchResult } from '../api/types'
import { VehiclesListPage } from './VehiclesListPage'

// KAN-152 — § ADR-056: search and sort are grid state and live in the URL.
// The Vehicles list kept both in component state, so a search could not
// be shared and a reload lost it.

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

const EMPTY_RESULT: VehicleSearchResult = {
  resolved: null,
  pickerCandidates: [],
  filtered: { items: [], nextCursor: null },
}

function renderAt(route: string) {
  return renderWithProviders(
    <>
      <LocationProbe />
      <VehiclesListPage />
    </>,
    { route },
  )
}

afterEach(() => cleanup())

describe('VehiclesListPage — search and sort are the URL (ADR-056, KAN-152)', () => {
  it('a pasted URL fills the search box, sends q to the API and keeps the sort', async () => {
    const backend = installFakeBackend([{ match: /^\/vehicle-mdm\/search$/, handler: () => EMPTY_RESULT }])

    renderAt('/vehicles?q=WVWZZZ&sort=vin:asc')

    expect(await screen.findByDisplayValue('WVWZZZ')).toBeInTheDocument()
    await waitFor(() => {
      const call = backend.callsTo(/^\/vehicle-mdm\/search$/, 'GET').at(-1)
      expect(call?.params.get('q')).toBe('WVWZZZ')
    })
    const search = screen.getByTestId('location-search').textContent ?? ''
    expect(search).toContain('q=WVWZZZ')
    expect(search).toContain('sort=vin%3Aasc')
  })

  it('typing a search writes q to the URL, and clearing it removes q', async () => {
    installFakeBackend([{ match: /^\/vehicle-mdm\/search$/, handler: () => EMPTY_RESULT }])

    renderAt('/vehicles')
    const box = await screen.findByRole('textbox')
    await userEvent.type(box, 'ZH 123')

    await waitFor(() => expect(screen.getByTestId('location-search').textContent).toContain('q=ZH+123'))

    await userEvent.clear(box)
    await waitFor(() => expect(screen.getByTestId('location-search').textContent).not.toContain('q='))
  })
})
