// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { CustomersMovedToContacts } from './CustomersMovedToContacts'

// KAN-464 PR-3 — /customers became /contacts; an old link lands on the same
// screen, its query and hash kept.
function Where() {
  const location = useLocation()
  return <p>{`${location.pathname}${location.search}${location.hash}`}</p>
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/customers/*" element={<CustomersMovedToContacts />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('the old /customers links (KAN-464)', () => {
  it.each([
    ['/customers', '/contacts'],
    ['/customers/new', '/contacts/new'],
    ['/customers/0192-abc?tab=history#top', '/contacts/0192-abc?tab=history#top'],
  ])('%s opens %s', (from, to) => {
    renderAt(from)
    expect(screen.getByText(to)).toBeInTheDocument()
  })
})
