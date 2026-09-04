// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer, email, phone } from '../../test/fixtures'
import type { CustomerEmailRead, CustomerPhoneRead } from '../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { CustomerCreateFlow } from '../CustomerCreateFlow'

// WP-6c exit criterion (ADR-067): "a customer with two mobiles, a work
// landline and a bounced email renders correctly in the create dialog and
// on the detail screen, the primary of each type is marked, marking a new
// primary unmarks the old in the same interaction, and closing a row keeps
// it readable with its reason." One RepeatableRowGroup, both surfaces.

const primaryPrefix = () => new RegExp(`^${i18n.t('customerDetail.contactPoints.primary')}`)
const pressedCount = (buttons: HTMLElement[]) => buttons.filter((b) => b.getAttribute('aria-pressed') === 'true').length

describe('contact channels — detail screen (ADR-067)', () => {
  function installCustomerBackend() {
    const phones: CustomerPhoneRead[] = [
      phone({ id: 'p-mob-1', type: 'mobile', value: '+41791110000', isPrimary: true }),
      phone({ id: 'p-mob-2', type: 'mobile', value: '+41792220000', isPrimary: false }),
      phone({ id: 'p-off-1', type: 'office', value: '+41443330000', isPrimary: true }),
    ]
    const emails: CustomerEmailRead[] = [
      email({ id: 'e-1', type: 'private', value: 'hans.muster@example.ch', isPrimary: true }),
      email({
        id: 'e-2',
        type: 'business',
        value: 'old.address@oldjob.ch',
        isPrimary: false,
        doNotUse: true,
        doNotUseReason: 'bounced',
      }),
    ]

    return installFakeBackend([
      { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1' }) },
      { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: phones }) },
      { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: emails }) },
      { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'PATCH',
        match: /^\/customers\/c1\/phones\/(.+)$/,
        handler: (req) => {
          const id = req.pathname.split('/').pop()!
          const patch = req.body as Partial<CustomerPhoneRead>
          const row = phones.find((p) => p.id === id)!
          Object.assign(row, patch)
          // The real service unsets every sibling of the same type in the
          // same transaction (_unset_other_primaries).
          if (patch.isPrimary) {
            for (const other of phones) {
              if (other.phoneType === row.phoneType && other.id !== row.id) other.isPrimary = false
            }
          }
          return row
        },
      },
    ])
  }

  function renderDetail() {
    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1' },
    )
  }

  it('renders both mobiles and the landline, marks the primary mobile, and a new primary unmarks the old in one interaction', async () => {
    const user = userEvent.setup()
    installCustomerBackend()
    renderDetail()

    // All three current numbers are on screen (PhoneInput shows the national
    // form, re-adding the trunk 0 it strips for E.164).
    expect(await screen.findByDisplayValue('0791110000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('0792220000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('0443330000')).toBeInTheDocument()

    // Two mobiles => a star on each; the lone landline gets none.
    let stars = screen.getAllByRole('button', { name: primaryPrefix() })
    expect(stars).toHaveLength(2)
    expect(stars[0]).toHaveAttribute('aria-pressed', 'true')
    expect(stars[1]).toHaveAttribute('aria-pressed', 'false')

    // Promote the second mobile.
    await user.click(stars[1])

    await waitFor(() => {
      stars = screen.getAllByRole('button', { name: primaryPrefix() })
      expect(pressedCount(stars)).toBe(1)
      expect(stars[1]).toHaveAttribute('aria-pressed', 'true')
      expect(stars[0]).toHaveAttribute('aria-pressed', 'false')
    })
  })

  it('a bounced address is closed, not deleted — kept behind the "former" toggle with its reason', async () => {
    const user = userEvent.setup()
    installCustomerBackend()
    renderDetail()

    await screen.findByDisplayValue('hans.muster@example.ch')
    expect(screen.queryByDisplayValue('old.address@oldjob.ch')).not.toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: new RegExp(i18n.t('customerDetail.contactPoints.former'), 'i') })
    await user.click(toggle)

    const dead = await screen.findByText('old.address@oldjob.ch')
    expect(dead).toHaveStyle({ textDecoration: 'line-through' })
    expect(screen.getByText('bounced')).toBeInTheDocument()
  })
})

describe('contact channels — create dialog (same RepeatableRowGroup, ADR-067)', () => {
  async function openCreateFlowAtStep2(user: ReturnType<typeof userEvent.setup>) {
    installFakeBackend([{ match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) }])
    renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
  }

  async function addPhone(user: ReturnType<typeof userEvent.setup>, type: string, national: string) {
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('customerDetail.contactPoints.addPhone'), 'i') }))
    const save = screen.getByRole('button', { name: i18n.t('customerDetail.contactPoints.save') })
    const addForm = save.closest('div') as HTMLElement
    // The native type <select> is the first combobox in the add row.
    await user.selectOptions(within(addForm).getAllByRole('combobox')[0], type)
    await user.type(within(addForm).getByLabelText(i18n.t('customerDetail.phoneInput.number')), national)
    await user.click(save)
  }

  it('two mobiles and a work landline render; the mobile primary is marked and re-marking moves it in one interaction', async () => {
    const user = userEvent.setup()
    await openCreateFlowAtStep2(user)

    await addPhone(user, 'mobile', '791110000')
    await addPhone(user, 'mobile', '792220000')
    await addPhone(user, 'office', '443330000')

    expect(screen.getByDisplayValue('0791110000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('0792220000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('0443330000')).toBeInTheDocument()

    let stars = screen.getAllByRole('button', { name: primaryPrefix() })
    expect(stars).toHaveLength(2)
    expect(pressedCount(stars)).toBe(1)
    expect(stars[0]).toHaveAttribute('aria-pressed', 'true')

    await user.click(stars[1])

    await waitFor(() => {
      stars = screen.getAllByRole('button', { name: primaryPrefix() })
      expect(pressedCount(stars)).toBe(1)
      expect(stars[1]).toHaveAttribute('aria-pressed', 'true')
      expect(stars[0]).toHaveAttribute('aria-pressed', 'false')
    })
  })
})
