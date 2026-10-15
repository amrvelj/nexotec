// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { ValuationCreateDialog } from './ValuationCreateDialog'

// KAN-266 — the valuation create carries one Idempotency-Key per submission:
// a retry after a failure keeps it, a corrected valuation or another opening
// gets a new one, and a success does not renew it (the dialog stays open
// after a success only when its host failed to use the valuation).

function install({ failFirst = false } = {}) {
  let creates = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
    {
      method: 'POST',
      match: /^\/valuations$/,
      handler: () => {
        creates += 1
        if (failFirst && creates === 1) {
          return status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } })
        }
        return { __status: 201, body: { id: `val-${creates}` } }
      },
    },
  ])
}

const keys = (backend: ReturnType<typeof install>) =>
  backend.callsTo(/^\/valuations$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

async function submit(user: ReturnType<typeof userEvent.setup>, finalOffer: string) {
  const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
  const field = within(dialog).getByLabelText(i18n.t('valuationCreate.finalOffer'), { exact: false })
  await user.clear(field)
  await user.type(field, finalOffer)
  await user.click(within(dialog).getByRole('button', { name: i18n.t('valuationCreate.submit') }))
}

afterEach(() => cleanup())

describe('ValuationCreateDialog — one Idempotency-Key per submission (KAN-266)', () => {
  it('a create retried after a failure keeps its key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('valuationCreate.submit') }))
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, retry] = keys(backend)
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
  })

  it('a corrected valuation after a failure is a new submission, under a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    await screen.findByText('Service unavailable')
    await submit(user, '11500')
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, corrected] = keys(backend)
    expect(first).toBeTruthy()
    expect(corrected).toBeTruthy()
    expect(corrected).not.toBe(first)
  })

  it('kept open after a success (its host failed to use the valuation), the same valuation replays it', async () => {
    // The offer's trade-in attach failed: the dialog stays open, and
    // "Erstellen" again must not create a second valuation.
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)

    await submit(user, '12000')
    await waitFor(() => expect(keys(backend)).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: i18n.t('valuationCreate.submit') }))
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, again] = keys(backend)
    expect(first).toBeTruthy()
    expect(again).toBe(first)
  })

  it('the same valuation after closing and reopening gets a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirst: true })
    function Host() {
      const [opened, setOpened] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setOpened(true)}>reopen</button>
          <ValuationCreateDialog opened={opened} onClose={() => setOpened(false)} onCreated={() => {}} />
        </>
      )
    }
    renderWithProviders(<Host />)

    await submit(user, '12000')
    await screen.findByText('Service unavailable')
    await user.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'reopen' }))
    await submit(user, '12000')
    await waitFor(() => expect(keys(backend)).toHaveLength(2))

    const [first, reopened] = keys(backend)
    expect(first).toBeTruthy()
    expect(reopened).toBeTruthy()
    expect(reopened).not.toBe(first)
  })
})

// KAN-351 — a VIN the API refuses (422 on `vin`, KAN-331) is named at the VIN
// input in the user's language; the dialog showed the API's English text.
describe('ValuationCreateDialog — a refused VIN is named at the VIN input (KAN-351)', () => {
  it('a 422 on vin shows the translated VIN rule under the VIN input', async () => {
    await i18n.changeLanguage('fr')
    try {
      const user = userEvent.setup()
      installFakeBackend([
        { method: 'GET', match: /^\/integrations\/capabilities\/valuation$/, handler: () => ({ capabilityCode: 'valuation', granted: true }) },
        {
          method: 'POST',
          match: /^\/valuations$/,
          handler: () =>
            status(422, {
              error: {
                code: 'unprocessable_entity',
                message: 'Request validation failed.',
                details: { errors: [{ loc: ['body', 'vin'], msg: 'Value error, invalid VIN', type: 'value_error' }] },
              },
            }),
        },
      ])
      renderWithProviders(<ValuationCreateDialog opened onClose={() => {}} onCreated={() => {}} />)
      const dialog = await screen.findByRole('dialog', { name: i18n.t('valuationCreate.title') })
      await user.type(within(dialog).getByLabelText(i18n.t('valuationCreate.vin')), 'WVWZZZ1KZAO331338')
      await submit(user, '12000')

      expect(await within(dialog).findByText(i18n.t('common.invalidVin'))).toBeInTheDocument()
      expect(within(dialog).getByLabelText(i18n.t('valuationCreate.vin'))).toHaveAttribute('aria-invalid', 'true')
      expect(within(dialog).queryByText('Request validation failed.')).toBeNull()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
