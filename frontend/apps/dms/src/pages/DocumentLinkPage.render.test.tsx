// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { DocumentLinkPage } from './DocumentLinkPage'

// KAN-217 PR-4 (ADR-193): the customer's secure link `/d?lang=…#<token>`. The
// token is read from the fragment, taken out of the address bar, and sent in a
// header — never in the URL the server sees.

const TOKEN = 'A'.repeat(43)

const PDF_ROUTE = {
  method: 'GET',
  match: /\/document-links\/current$/,
  handler: () =>
    new Response('%PDF-1.7', {
      status: 200,
      headers: { 'content-type': 'application/pdf', 'content-disposition': 'inline; filename="OF-2026-0042-v1.pdf"' },
    }),
}

function renderAt(search: string, hash: string) {
  window.history.replaceState(null, '', `/d${search}${hash}`)
  return renderWithProviders(
    <Routes>
      <Route path="/d" element={<DocumentLinkPage />} />
    </Routes>,
    { route: `/d${search}` },
  )
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:document')
  URL.revokeObjectURL = vi.fn()
})

afterEach(async () => {
  await i18n.changeLanguage('de')
  window.history.replaceState(null, '', '/')
  window.sessionStorage.clear()
})

describe('DocumentLinkPage (KAN-217 PR-4)', () => {
  it('fetches the document with the token in a header and offers to open and download it', async () => {
    const backend = installFakeBackend([PDF_ROUTE])
    renderAt('', `#${TOKEN}`)

    const download = await screen.findByRole('link', { name: 'Herunterladen' })
    expect(download).toHaveAttribute('href', 'blob:document')
    expect(download).toHaveAttribute('download', 'OF-2026-0042-v1.pdf')
    expect(screen.getByRole('link', { name: 'Öffnen' })).toHaveAttribute('href', 'blob:document')

    const call = backend.calls.find((c) => c.pathname.endsWith('/document-links/current'))
    expect(call?.headers.get('X-Document-Link')).toBe(TOKEN)
    expect(`${call?.path}?${call?.params.toString()}`).not.toContain(TOKEN)
    // The token left the address bar.
    expect(window.location.hash).toBe('')
  })

  it('opens in the language the message was written in', async () => {
    installFakeBackend([PDF_ROUTE])
    renderAt('?lang=it', `#${TOKEN}`)

    expect(await screen.findByRole('link', { name: 'Scarica' })).toBeInTheDocument()
    expect(window.location.search).toBe('?lang=it')
    expect(window.location.hash).toBe('')
  })

  it('opens the document again on a reload of the cleaned address', async () => {
    installFakeBackend([PDF_ROUTE])
    const first = renderAt('', `#${TOKEN}`)
    await screen.findByRole('link', { name: 'Herunterladen' })
    first.unmount()

    const backend = installFakeBackend([PDF_ROUTE])
    renderAt('', '')
    await screen.findByRole('link', { name: 'Herunterladen' })
    const call = backend.calls.find((c) => c.pathname.endsWith('/document-links/current'))
    expect(call?.headers.get('X-Document-Link')).toBe(TOKEN)
  })

  it('says a refused link is invalid or expired, in the language shown', async () => {
    installFakeBackend([
      {
        method: 'GET',
        match: /\/document-links\/current$/,
        handler: () => status(404, { error: { code: 'not_found', message: 'Not found.', details: null } }),
      },
    ])
    renderAt('?lang=fr', `#${TOKEN}`)

    expect(await screen.findByText(/Ce lien n'est pas valable ou a expiré\./)).toBeInTheDocument()
  })

  it('asks nothing without a token', async () => {
    const backend = installFakeBackend([])
    renderAt('', '')

    expect(await screen.findByRole('alert')).toHaveTextContent('Dieser Link ist ungültig oder abgelaufen.')
    expect(backend.calls.filter((c) => c.pathname.includes('document-links'))).toEqual([])
  })
})
