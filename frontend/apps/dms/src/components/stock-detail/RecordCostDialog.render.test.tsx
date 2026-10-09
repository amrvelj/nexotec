// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { RecordCostDialog } from './RecordCostDialog'

// KAN-266 — `sourceRef` is the ledger's own idempotency key
// (services/ledger.py::record_cost). It was minted per click, so a retry
// after a failure (a response lost on the way back) booked the cost twice.
// Now it is one per request: a retry keeps it, a corrected entry gets a
// new one. A failed save is shown, so the user knows to send it again.

type Entry = { category: string; amount: number; occurredAt: string; sourceRef: string }

function renderDialog() {
  const submitted: Entry[] = []
  const onSubmit = vi.fn(async (entry: Entry) => {
    submitted.push(entry)
    if (submitted.length === 1) throw new Error('network')
  })
  renderWithProviders(<RecordCostDialog opened onClose={() => {}} onSubmit={onSubmit} />)
  return { submitted }
}

async function setAmount(user: ReturnType<typeof userEvent.setup>, amount: string) {
  const field = screen.getByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.amount') })
  await user.clear(field)
  await user.type(field, amount)
}

async function fill(user: ReturnType<typeof userEvent.setup>, amount: string) {
  await user.click(screen.getByRole('textbox', { name: i18n.t('stockDetail.wagenbuch.fields.category') }))
  await user.click((await screen.findAllByRole('option'))[0])
  await setAmount(user, amount)
}

const submit = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('button', { name: i18n.t('stockDetail.wagenbuch.submit') }))

afterEach(() => cleanup())

describe('RecordCostDialog — one sourceRef per request (KAN-266)', () => {
  it('a failed save is shown, and its retry carries the same sourceRef', async () => {
    const user = userEvent.setup()
    const { submitted } = renderDialog()
    await fill(user, '350')

    await submit(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('stockDetail.wagenbuch.error'))
    await submit(user)
    await waitFor(() => expect(submitted).toHaveLength(2))

    expect(submitted[0].sourceRef).toBeTruthy()
    expect(submitted[1].sourceRef).toBe(submitted[0].sourceRef)
  })

  it('a corrected amount after a failure is another request and gets a new sourceRef', async () => {
    const user = userEvent.setup()
    const { submitted } = renderDialog()
    await fill(user, '350')
    await submit(user)
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await setAmount(user, '420')
    await submit(user)
    await waitFor(() => expect(submitted).toHaveLength(2))

    expect(submitted[1].amount).toBe(420)
    expect(submitted[1].sourceRef).toBeTruthy()
    expect(submitted[1].sourceRef).not.toBe(submitted[0].sourceRef)
  })
})
