// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { customer, email, phone } from '../../../../test/fixtures'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { CustomerCreateFlow } from '../CustomerCreateFlow'

// KAN-460 (Anto's answer on the ticket, option A) — the one-line column grid needs
// about 872 px of row group (about 910 px of card) at the phone editor's
// width, but the contact card was
// one of two side by side (530–610 px on a 1440–1600 px screen) and the
// create dialog gives it 588 px: on ordinary screens the rows always showed
// stacked. The «Kontaktpunkte» card now takes the customer screen's full
// width, and the create dialog uses the compact country select («🇨🇭 +41»).
// jsdom has no layout: these fake the row group's measured width (the kit
// reads it with getBoundingClientRect): 900 px (just above the template's
// 872 px) and 760 px on the detail screen, 560 px in the dialog.

/** Every element reports `width` — the row group's own width is what the kit reads. */
function fakeGroupWidth(width: number) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width, height: 40, top: 0, left: 0, right: width, bottom: 40, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect)
}

afterEach(() => vi.restoreAllMocks())

const layoutOf = (el: HTMLElement) => (el.closest('[data-row-layout]') as HTMLElement).dataset.rowLayout
const cellOf = (el: HTMLElement, part: string) => el.closest(`[data-row-part="${part}"]`) as HTMLElement

function renderDetail() {
  installFakeBackend([
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [phone({ id: 'p1', type: 'mobile', value: '+41791110000', isPrimary: true })] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [email({ id: 'e1', type: 'personal', value: 'hans.muster@example.ch', isPrimary: true })] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' },
  )
}

describe('contact points on the customer screen take its full width (KAN-460, option A)', () => {
  it('the «Kontaktpunkte» card spans the whole row of the overview grid', async () => {
    renderDetail()
    const number = await screen.findByDisplayValue('0791110000')
    const card = number.closest('[data-card="contact-points"]') as HTMLElement
    expect(card).not.toBeNull()
    expect(card.style.gridColumn).toBe('1 / -1')
  })

  it('in a 900 px row group the phone and e-mail rows sit on the one-line template with a header', async () => {
    fakeGroupWidth(900)
    renderDetail()
    const number = await screen.findByDisplayValue('0791110000')
    expect(layoutOf(number)).toBe('template')
    expect(layoutOf(screen.getByDisplayValue('hans.muster@example.ch'))).toBe('template')
    expect(document.querySelectorAll('[data-row-part="header"]')).toHaveLength(2)
  })

  it('in a 760 px row group the rows stack, every cell with its own label', async () => {
    fakeGroupWidth(760)
    renderDetail()
    const number = await screen.findByDisplayValue('0791110000')
    expect(layoutOf(number)).toBe('stacked')
    expect(document.querySelector('[data-row-part="header"]')).toBeNull()
    expect(cellOf(number, 'value')).toHaveTextContent(i18n.t('customerDetail.contactPoints.phoneValueColumn'))
    const label = screen.getAllByRole('textbox', { name: i18n.t('customerDetail.contactPoints.labelColumn') })[0]
    expect(cellOf(label, 'label')).toHaveTextContent(i18n.t('customerDetail.contactPoints.labelColumn'))
  })
})

describe('the create dialog uses the compact country select (KAN-460, option A)', () => {
  async function openPhoneBlock(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
    installFakeBackend([{ match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) }])
    renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
    const heading = await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
    return heading.parentElement as HTMLElement
  }

  it('at 560 px the add row stacks with its labels, and the country reads «🇨🇭 +41» while the list names every country', async () => {
    fakeGroupWidth(560)
    const user = userEvent.setup({ delay: null })
    const phoneBlock = await openPhoneBlock(user)
    await user.click(within(phoneBlock).getByRole('button', { name: new RegExp(i18n.t('customerDetail.contactPoints.addPhone'), 'i') }))

    const number = within(phoneBlock).getByRole('textbox', { name: i18n.t('customerDetail.phoneInput.number') })
    expect(layoutOf(number)).toBe('stacked')
    expect(cellOf(number, 'value')).toHaveTextContent(i18n.t('customerDetail.contactPoints.phoneValueColumn'))
    const type = within(phoneBlock).getByRole('combobox', { name: i18n.t('customerDetail.contactPoints.typeColumn') })
    expect(cellOf(type, 'type')).toHaveTextContent(i18n.t('customerDetail.contactPoints.typeColumn'))

    const country = within(phoneBlock).getByRole('textbox', { name: i18n.t('customerDetail.phoneInput.country') }) as HTMLInputElement
    expect(country.value).toBe('🇨🇭 +41')
    await user.click(country)
    expect(await screen.findByRole('option', { name: `🇨🇭 ${i18n.t('customerDetail.countries.switzerland')} (+41)` })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: `🇱🇮 ${i18n.t('customerDetail.countries.liechtenstein')} (+423)` })).toBeInTheDocument()
  })
})
