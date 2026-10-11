// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import en from '../../i18n/locales/en.json'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import { customer, email, phone } from '../../test/fixtures'
import { MergeCustomerModal } from './MergeCustomerModal'

// KAN-268 — the merge dialog was English in every UI language, and it is the
// one irreversible customer action: its warning is the text the user must
// understand. KAN-266 — the merge POST carries If-Match and one
// Idempotency-Key per submission: a retry after a failure reuses it, a new
// opening of the dialog gets its own.

const MARKER = /⚠ MISSING I18N KEY/

const DUPLICATE = customer({ id: 'dup-1', customerNumber: 'K-000001', firstName: 'Hans', lastName: 'Muster', version: 4 })
const SURVIVOR = customer({ id: 'surv-1', customerNumber: 'K-000002', firstName: 'Hans', lastName: 'Mustermann' })

// Every English string the dialog shows — its own, and the labels of the
// customer-type and lifecycle badges it renders — none may show in another
// language.
const ENGLISH_PHRASES = [
  ...Object.entries(en.customerDetail.merge)
    .filter(([key]) => key !== 'intro')
    .map(([, value]) => value),
  en.customerEnums.customerType.individual,
  en.customerEnums.lifecycleStatus.active,
]
const ENGLISH_INTRO = /should be merged into/

function install({ failFirstMerge = false } = {}) {
  let merges = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/customers$/, handler: () => ({ items: [DUPLICATE, SURVIVOR], nextCursor: null }) },
    { method: 'GET', match: /^\/customers\/surv-1\/phones$/, handler: () => ({ items: [phone({ customerId: 'surv-1', value: '+41791112233' })] }) },
    { method: 'GET', match: /^\/customers\/surv-1\/emails$/, handler: () => ({ items: [email({ customerId: 'surv-1', value: 'hans@example.ch' })] }) },
    {
      method: 'POST',
      match: /^\/customers\/dup-1\/merge$/,
      handler: () => {
        merges += 1
        // A gateway timeout: an HTML body, not the API's error envelope (KAN-271).
        if (failFirstMerge && merges === 1) {
          return new Response('<html><body>504 Gateway Time-out</body></html>', { status: 504, headers: { 'content-type': 'text/html' } })
        }
        return status(200, { ...DUPLICATE, lifecycleStatus: 'merged' })
      },
    },
  ])
}

function renderModal(props: { onMerged?: (id: string) => void } = {}) {
  const onMerged = props.onMerged ?? (() => {})
  let setOpenedState: (opened: boolean) => void = () => {}
  function Harness() {
    const [opened, setOpened] = useState(true)
    setOpenedState = setOpened
    return <MergeCustomerModal opened={opened} onClose={() => {}} customer={DUPLICATE} phones={[]} emails={[]} onMerged={onMerged} />
  }
  const result = renderWithProviders(<Harness />)
  return { ...result, setOpened: (opened: boolean) => act(() => setOpenedState(opened)) }
}

async function pickSurvivor(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText(i18n.t('customerDetail.merge.searchPlaceholder')), 'Muster')
  await user.click(await screen.findByText('Hans Mustermann'))
  await screen.findByText(i18n.t('customerDetail.merge.irreversibleTitle'))
}

async function confirmAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  const checkbox = screen.getByLabelText(i18n.t('customerDetail.merge.confirm'))
  if (!(checkbox as HTMLInputElement).checked) await user.click(checkbox)
  await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.merge.submit') }))
}

const mergeCalls = (backend: ReturnType<typeof install>) => backend.callsTo(/^\/customers\/dup-1\/merge$/, 'POST')

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('MergeCustomerModal — every string is translated (KAN-268)', () => {
  for (const lng of ['fr', 'it', 'de'] as const) {
    it(`renders both steps in ${lng} with no English and no missing-key marker`, async () => {
      await i18n.changeLanguage(lng)
      const user = userEvent.setup()
      install()
      renderModal()

      const dialog = await screen.findByRole('dialog')
      expect(screen.getByText(i18n.t('customerDetail.merge.title'))).toBeInTheDocument()
      expect(dialog.textContent).toContain('Hans Muster')
      expect(dialog.textContent).not.toMatch(ENGLISH_INTRO)

      await pickSurvivor(user)
      expect(screen.getByText(i18n.t('customerDetail.merge.duplicateColumn'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('customerDetail.merge.survivorColumn'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('customerDetail.merge.irreversibleBody'))).toBeInTheDocument()
      expect(screen.getByRole('button', { name: i18n.t('customerDetail.merge.submit') })).toBeInTheDocument()
      expect(screen.getAllByText(i18n.t('customerEnums.customerType.individual'))).toHaveLength(2)
      expect(screen.getAllByText(i18n.t('customerEnums.lifecycleStatus.active'))).toHaveLength(2)

      const text = screen.getByRole('dialog').textContent ?? ''
      expect(text).not.toMatch(MARKER)
      for (const phrase of ENGLISH_PHRASES) {
        expect(text.includes(phrase), `"${phrase}" leaked in ${lng}`).toBe(false)
      }
    })
  }

  it('shows the translated empty state when the search finds no other customer', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([{ method: 'GET', match: /^\/customers$/, handler: () => ({ items: [DUPLICATE], nextCursor: null }) }])
    renderModal()

    await user.type(screen.getByPlaceholderText(i18n.t('customerDetail.merge.searchPlaceholder')), 'Muster')
    expect(await screen.findByText(i18n.t('customerDetail.merge.noMatches'))).toBeInTheDocument()
    expect(screen.queryByText(en.customerDetail.merge.noMatches)).toBeNull()
  })
})

describe('MergeCustomerModal — the intro names the duplicate verbatim', () => {
  it('renders a name that looks like markup as text', async () => {
    installFakeBackend([])
    const odd = customer({ ...DUPLICATE, customerType: 'business', companyName: 'Garage <strong>Alpha</strong> & Söhne AG' })
    renderWithProviders(<MergeCustomerModal opened onClose={() => {}} customer={odd} phones={[]} emails={[]} onMerged={() => {}} />)

    const name = await screen.findByText('Garage <strong>Alpha</strong> & Söhne AG')
    expect(name.tagName).toBe('STRONG')
  })
})

describe('MergeCustomerModal — the merge POST (KAN-266, KAN-271)', () => {
  it('sends If-Match and an Idempotency-Key, and names the survivor', async () => {
    const user = userEvent.setup()
    const onMerged = vi.fn()
    const backend = install()
    renderModal({ onMerged })

    await pickSurvivor(user)
    await confirmAndSubmit(user)

    await waitFor(() => expect(onMerged).toHaveBeenCalledWith('surv-1'))
    const [call] = mergeCalls(backend)
    expect(call.body).toEqual({ duplicateOfCustomerId: 'surv-1' })
    expect(call.headers.get('If-Match')).toBe('4')
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a gateway timeout shows a translated message in fr, and the retry reuses the key', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const onMerged = vi.fn()
    const backend = install({ failFirstMerge: true })
    renderModal({ onMerged })

    await pickSurvivor(user)
    await confirmAndSubmit(user)

    expect(await screen.findByText(i18n.t('common.requestFailed'))).toBeInTheDocument()
    expect(screen.queryByText(en.common.requestFailed)).toBeNull()
    expect(screen.queryByText('Something went wrong.')).toBeNull()
    expect(onMerged).not.toHaveBeenCalled()

    await confirmAndSubmit(user)
    await waitFor(() => expect(onMerged).toHaveBeenCalledWith('surv-1'))

    const [first, retry] = mergeCalls(backend)
    expect(first.headers.get('Idempotency-Key')).toBeTruthy()
    expect(retry.headers.get('Idempotency-Key')).toBe(first.headers.get('Idempotency-Key'))
  })

  it('a dropped connection (no response at all) shows the translated merge error', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    installFakeBackend([
      { method: 'GET', match: /^\/customers$/, handler: () => ({ items: [DUPLICATE, SURVIVOR], nextCursor: null }) },
      { method: 'GET', match: /^\/customers\/surv-1\/(phones|emails)$/, handler: () => ({ items: [] }) },
      {
        method: 'POST',
        match: /^\/customers\/dup-1\/merge$/,
        handler: () => {
          throw new TypeError('Failed to fetch')
        },
      },
    ])
    renderModal()

    await pickSurvivor(user)
    await confirmAndSubmit(user)
    expect(await screen.findByText(i18n.t('customerDetail.merge.error'))).toBeInTheDocument()
    expect(i18n.t('customerDetail.merge.error')).not.toBe(en.customerDetail.merge.error)
  })

  it('opening the dialog again is a new submission under a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ failFirstMerge: true })
    const { setOpened } = renderModal()

    await pickSurvivor(user)
    await confirmAndSubmit(user)
    await screen.findByText(i18n.t('common.requestFailed'))

    setOpened(false)
    // Let the search's 300 ms debounce settle on the cleared query, as it
    // does for a user who closes the dialog and comes back to it.
    await act(() => new Promise((resolve) => setTimeout(resolve, 400)))
    setOpened(true)
    await pickSurvivor(user)
    await confirmAndSubmit(user)
    await waitFor(() => expect(mergeCalls(backend)).toHaveLength(2))

    const [first, second] = mergeCalls(backend)
    expect(second.headers.get('Idempotency-Key')).toBeTruthy()
    expect(second.headers.get('Idempotency-Key')).not.toBe(first.headers.get('Idempotency-Key'))
  })
})
