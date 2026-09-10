// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { customerAddress, email, phone } from '../../test/fixtures'
import { ContactChannelsSummary, PreferredChannelBadge } from './ContactChannelsSummary'

// KAN-54 / FR-18 region 3 — the contact card's read view: live tel:/mailto:
// links, the primary row of the PREFERRED kind marked, a header badge that
// survives the customer having no row of that kind, and dead rows never
// linked.

const mobile = (over = {}) => phone({ type: 'mobile', value: '+41 79 111 22 33', isPrimary: true, ...over })
const work = (over = {}) => phone({ type: 'work', value: '+41 44 000 00 00', ...over })
const personalEmail = (over = {}) => email({ type: 'personal', value: 'ada@byron.example', isPrimary: true, ...over })

describe('ContactChannelsSummary', () => {
  it('marks the primary row of the preferred kind, and only that row', () => {
    renderWithProviders(
      <ContactChannelsSummary phones={[mobile(), work()]} emails={[personalEmail()]} address={null} preferredChannel="phone" />,
    )
    const marks = screen.getAllByLabelText(i18n.t('customerDetail.contactPoints.preferredMark'))
    expect(marks).toHaveLength(1)
    // the mark sits next to the primary mobile, not the work number
    const markedRow = marks[0].parentElement!
    expect(within(markedRow).getByText('+41 79 111 22 33')).toBeInTheDocument()
  })

  it('renders phone numbers as tel: links with whitespace stripped, keeping the formatted text', () => {
    renderWithProviders(
      <ContactChannelsSummary phones={[mobile()]} emails={[]} address={null} preferredChannel={null} />,
    )
    const link = screen.getByRole('link', { name: '+41 79 111 22 33' })
    expect(link).toHaveAttribute('href', 'tel:+41791112233')
  })

  it('renders email addresses as mailto: links', () => {
    renderWithProviders(
      <ContactChannelsSummary phones={[]} emails={[personalEmail()]} address={null} preferredChannel={null} />,
    )
    expect(screen.getByRole('link', { name: 'ada@byron.example' })).toHaveAttribute('href', 'mailto:ada@byron.example')
  })

  it('never shows or marks a doNotUse row, and it is not a live link', () => {
    renderWithProviders(
      <ContactChannelsSummary
        phones={[mobile({ doNotUse: true })]}
        emails={[]}
        address={null}
        preferredChannel="phone"
      />,
    )
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(i18n.t('customerDetail.contactPoints.preferredMark'))).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('customerDetail.contactPoints.none'))).toBeInTheDocument()
  })

  it('preferred = post with a domicile address marks it, and the address is not a link', () => {
    renderWithProviders(
      <ContactChannelsSummary
        phones={[]}
        emails={[]}
        address={customerAddress({ isPrimary: true })}
        preferredChannel="post"
      />,
    )
    expect(screen.getByLabelText(i18n.t('customerDetail.contactPoints.preferredMark'))).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('preferred = post with no address at all does not crash and marks nothing', () => {
    renderWithProviders(
      <ContactChannelsSummary phones={[mobile()]} emails={[]} address={null} preferredChannel="post" />,
    )
    expect(screen.queryByLabelText(i18n.t('customerDetail.contactPoints.preferredMark'))).not.toBeInTheDocument()
    // the phone still renders (it just isn't the preferred kind)
    expect(screen.getByRole('link', { name: '+41 79 111 22 33' })).toBeInTheDocument()
  })
})

describe('PreferredChannelBadge', () => {
  it('shows the translated preferred channel when set', () => {
    renderWithProviders(<PreferredChannelBadge preferredChannel="whatsapp" />)
    expect(screen.getByText(i18n.t('customerEnums.preferredChannel.whatsapp'))).toBeInTheDocument()
  })

  it('shows a muted "no preferred channel" when unset', () => {
    renderWithProviders(<PreferredChannelBadge preferredChannel={null} />)
    expect(screen.getByText(i18n.t('customerDetail.contactPoints.noPreferred'))).toBeInTheDocument()
  })
})
