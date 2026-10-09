// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { SalesOfferRead } from '../api/types'
import { OfferGenerateReviewModal } from './OfferGenerateReviewModal'

// KAN-266 — building the offer document and confirming it each carry an
// Idempotency-Key. A retry after a failure keeps its key: the server
// replays the document it built (no second version) or the finalize it ran
// (no version conflict). "Neu erzeugen" and a fresh opening are new
// submissions.

const OFFER = {
  id: 'o1',
  offerNumber: 'O-000001',
  status: 'draft',
  version: 5,
  costBasis: null,
  margin: null,
  discountAmount: null,
} as unknown as SalesOfferRead

const unavailable = () => status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } })

function install({ failFirstBuild = false, failFirstFinalize = false } = {}) {
  let builds = 0
  let finalizes = 0
  return installFakeBackend([
    {
      method: 'POST',
      match: /^\/sales\/offers\/o1\/documents$/,
      handler: () => {
        builds += 1
        if (failFirstBuild && builds === 1) return unavailable()
        return { __status: 201, body: { id: `d${builds}`, version: builds, correspondenceLanguage: 'de' } }
      },
    },
    { method: 'GET', match: /^\/sales\/documents\/d\d+\/pdf$/, handler: () => ({}) },
    {
      method: 'POST',
      match: /^\/sales\/offers\/o1\/finalize$/,
      handler: () => {
        finalizes += 1
        if (failFirstFinalize && finalizes === 1) return unavailable()
        return { ...OFFER, status: 'open', version: 6 }
      },
    },
  ])
}

const buildKeys = (backend: ReturnType<typeof install>) =>
  backend.callsTo(/\/documents$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

const buildButton = () => screen.findByRole('button', { name: i18n.t('offerWorkspace.generate.build') })

afterEach(() => cleanup())

describe('OfferGenerateReviewModal — one Idempotency-Key per submission (KAN-266)', () => {
  it('a build retried after a failure keeps its key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirstBuild: true })
    renderWithProviders(<OfferGenerateReviewModal opened offer={OFFER} onClose={() => {}} onFinalized={() => {}} />)

    await user.click(await buildButton())
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
    await user.click(await buildButton())
    await waitFor(() => expect(buildKeys(backend)).toHaveLength(2))

    const [first, retry] = buildKeys(backend)
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
  })

  it('"Neu erzeugen" after a successful build is a new submission, under a new key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<OfferGenerateReviewModal opened offer={OFFER} onClose={() => {}} onFinalized={() => {}} />)

    await user.click(await buildButton())
    await user.click(await screen.findByRole('button', { name: i18n.t('offerWorkspace.generate.regenerate') }))
    await user.click(await buildButton())
    await waitFor(() => expect(buildKeys(backend)).toHaveLength(2))

    const [first, second] = buildKeys(backend)
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('a build after closing and reopening the modal gets a new key, even after a failure', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirstBuild: true })
    function Host() {
      const [opened, setOpened] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setOpened(true)}>reopen</button>
          <OfferGenerateReviewModal opened={opened} offer={OFFER} onClose={() => setOpened(false)} onFinalized={() => {}} />
        </>
      )
    }
    renderWithProviders(<Host />)

    await user.click(await buildButton())
    await screen.findByText('Service unavailable')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'reopen' }))
    await user.click(await buildButton())
    await waitFor(() => expect(buildKeys(backend)).toHaveLength(2))

    const [first, second] = buildKeys(backend)
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('a confirm retried after a failure keeps its key, beside its If-Match', async () => {
    const user = userEvent.setup()
    const onFinalized = vi.fn()
    const backend = install({ failFirstFinalize: true })
    renderWithProviders(<OfferGenerateReviewModal opened offer={OFFER} onClose={() => {}} onFinalized={onFinalized} />)

    await user.click(await buildButton())
    const confirm = await screen.findByRole('button', { name: i18n.t('offerWorkspace.generate.confirm') })
    await user.click(confirm)
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
    await user.click(confirm)
    await waitFor(() => expect(onFinalized).toHaveBeenCalled())

    const [first, retry] = backend.callsTo(/\/finalize$/, 'POST')
    expect(first.headers.get('Idempotency-Key')).toBeTruthy()
    expect(retry.headers.get('Idempotency-Key')).toBe(first.headers.get('Idempotency-Key'))
    expect(retry.headers.get('If-Match')).toBe('5')
  })
})
