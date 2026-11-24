// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../../../test/fakeBackend'
import { contact } from '../../../../test/fixtures'
import { GATED_CAPABILITIES } from '../../../../auth/gatedCapabilities'
import type { ContactRead } from '../../../../api/types'
import { ContactDetailPage } from '../../pages/ContactDetailPage'
import { ContactCreateFlow } from '../ContactCreateFlow'
import { buildContactRowMenu } from '../contactRowMenu'

// KAN-464 PR-2 — Q3 (Anto, 2026-11-14, A): inventory writes contacts and
// gives the supplier role; the customer role and its fields (status,
// advisor, credit block, merging a contact) stay with sales and
// aftersales (`contact_customer_role`). The SPA never offers what the
// server would refuse.

const INVENTORY_CAPABILITIES = GATED_CAPABILITIES.filter((name) => name !== 'contact_customer_role')

function inventorySession(): FakeRoute {
  return {
    method: 'GET',
    match: /\/auth\/me$/,
    handler: () => ({
      user: { id: 'u-1', firstName: 'Ida', lastName: 'Einkauf', email: 'ida@example.ch', accessRoles: ['inventory'], isDealerManager: false },
      activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
      memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
      entitlements: ['sales', 'inventory', 'aftersales', 'parts', 'finance', 'valuation'],
      writableCapabilities: INVENTORY_CAPABILITIES,
    }),
  }
}

function routes(overrides: Partial<ContactRead> = {}): FakeRoute[] {
  return [
    inventorySession(),
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/advisor-options$/, handler: () => ({ items: [{ id: 'user-7', label: 'Rey Ortiz' }] }) },
    { match: /^\/contacts\/c1$/, handler: () => contact({ id: 'c1', ...overrides }) },
  ]
}

const role = (code: string) => i18n.t(`contactEnums.role.${code}`)

describe('an inventory user and the customer role (KAN-464 Q3)', () => {
  it('sees a contact\'s status and advisor but cannot edit them, and cannot take the customer role away', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes({ roles: ['customer'], lifecycleStatus: 'prospect', advisorId: 'user-7', advisorLabel: 'Rey Ortiz' }),
      { method: 'PATCH', match: /^\/contacts\/c1$/, handler: (req) => contact({ id: 'c1', ...(req.body as object) }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/contacts/:id" element={<ContactDetailPage />} />
      </Routes>,
      { route: '/contacts/c1' },
    )
    await screen.findAllByText(i18n.t('contactDetail.overview.cards.relationship'))
    // Read-only: the value is shown, no inline editor opens on it.
    const advisorRow = screen.getByText(i18n.t('contactDetail.overview.fields.advisor')).parentElement!
    expect(within(advisorRow).getByText('Rey Ortiz')).toBeInTheDocument()
    expect(within(advisorRow).queryByRole('button')).toBeNull()

    await user.click(screen.getByRole('textbox', { name: i18n.t('contactDetail.overview.fields.roles') }))
    const listbox = await screen.findByRole('listbox')
    const contactOption = within(listbox).getByRole('option', { name: role('customer') })
    expect(contactOption).toHaveAttribute('data-combobox-disabled')
    // The supplier role stays theirs to give.
    await user.click(within(listbox).getByRole('option', { name: role('supplier') }))
    await waitFor(() => expect(backend.callsTo(/^\/contacts\/c1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/^\/contacts\/c1$/, 'PATCH')[0].body).toEqual({ roles: ['customer', 'supplier'] })
  })

  it('creates a supplier: the customer role is shown, not chosen, and no status is sent', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      inventorySession(),
      { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/contacts$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
    ])
    const created = vi.fn()
    const { container } = renderWithProviders(<ContactCreateFlow onSuccess={created} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.next') }))
    await screen.findByText(i18n.t('contactDetail.contactPoints.phoneNumbers'))
    await waitFor(() => expect(screen.queryByText(i18n.t('contactCreate.fields.lifecycleStatus'))).toBeNull())

    await user.type(container.querySelector<HTMLInputElement>('input[data-path="firstName"]')!, 'Anna')
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('contactDetail.contactPoints.addEmail'), 'i') }))
    await user.type(container.querySelector<HTMLInputElement>('input[type="email"]')!, 'anna@example.ch')
    await user.click(screen.getByRole('button', { name: i18n.t('contactDetail.contactPoints.save') }))
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.submit') }))

    await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
    const body = backend.callsTo(/^\/contacts$/, 'POST')[0].body as Record<string, unknown>
    expect(body.roles).toEqual(['supplier'])
    expect(body).not.toHaveProperty('lifecycleStatus')
  })
})

describe('the row menu without the customer role\'s write (KAN-464 Q3)', () => {
  const t = (key: string) => key
  const handlers = {
    onNewOffer: vi.fn(),
    onNewContract: vi.fn(),
    onCopyContactNumber: vi.fn(),
    onToggleDoNotContact: vi.fn(),
    onManageCreditBlock: vi.fn(),
    onMergeInto: vi.fn(),
  }
  const base = { lifecycleStatus: 'active' as const, creditBlock: false, creditBlockReason: null }
  const menu = (c: Parameters<typeof buildContactRowMenu>[1], mayWriteCustomerRole: boolean) =>
    buildContactRowMenu(t, c, handlers, { mayWriteCustomerRole })
  const labels = (m: ReturnType<typeof menu>) => [...(m.overflow.edit ?? []), ...(m.overflow.createFrom ?? [])].map((a) => a.label)

  it('offers no credit block and no merge of a contact; do-not-contact and merging suppliers stay', () => {
    const contactMenu = labels(menu({ ...base, roles: ['customer'] }, false))
    expect(contactMenu).not.toContain('contactRowMenu.setCreditBlock')
    expect(contactMenu).not.toContain('contactRowMenu.mergeInto')
    expect(contactMenu).toContain('contactRowMenu.setDoNotContact')
    expect(labels(menu({ ...base, creditBlock: true, roles: ['customer'] }, false))).not.toContain('contactRowMenu.removeCreditBlock')
    expect(labels(menu({ ...base, roles: ['supplier'] }, false))).toContain('contactRowMenu.mergeInto')

    // Lifting do-not-contact on a contact chooses its status: disabled, with the reason.
    const lift = (roles: ('customer' | 'supplier')[], may: boolean) =>
      (menu({ ...base, lifecycleStatus: 'do_not_contact', roles }, may).overflow.edit ?? []).find(
        (a) => a.label === 'contactRowMenu.removeDoNotContact',
      )!
    expect(lift(['customer'], false)).toMatchObject({ disabled: true, disabledReason: 'contactRowMenu.removeDoNotContactCustomerRole' })
    expect(lift(['supplier'], false).disabled).toBeFalsy()
    expect(lift(['customer'], true).disabled).toBeFalsy()

    const salesMenu = labels(menu({ ...base, roles: ['customer'] }, true))
    expect(salesMenu).toContain('contactRowMenu.setCreditBlock')
    expect(salesMenu).toContain('contactRowMenu.mergeInto')
  })
})
