// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../../../test/fakeBackend'
import { customer } from '../../../../test/fixtures'
import { GATED_CAPABILITIES } from '../../../../auth/gatedCapabilities'
import type { CustomerRead } from '../../../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { CustomerCreateFlow } from '../CustomerCreateFlow'
import { buildCustomerRowMenu } from '../customerRowMenu'

// KAN-464 PR-2 — Q3 (Anto, 2026-11-14, A): inventory writes contacts and
// gives the supplier role; the customer role and its fields (status,
// advisor, credit block, merging a customer) stay with sales and
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

function routes(overrides: Partial<CustomerRead> = {}): FakeRoute[] {
  return [
    inventorySession(),
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/advisor-options$/, handler: () => ({ items: [{ id: 'user-7', label: 'Rey Ortiz' }] }) },
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', ...overrides }) },
  ]
}

const role = (code: string) => i18n.t(`customerEnums.role.${code}`)

describe('an inventory user and the customer role (KAN-464 Q3)', () => {
  it('sees a customer\'s status and advisor but cannot edit them, and cannot take the customer role away', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes({ roles: ['customer'], lifecycleStatus: 'prospect', advisorId: 'user-7', advisorLabel: 'Rey Ortiz' }),
      { method: 'PATCH', match: /^\/customers\/c1$/, handler: (req) => customer({ id: 'c1', ...(req.body as object) }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1' },
    )
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.relationship'))
    // Read-only: the value is shown, no inline editor opens on it.
    const advisorRow = screen.getByText(i18n.t('customerDetail.overview.fields.advisor')).parentElement!
    expect(within(advisorRow).getByText('Rey Ortiz')).toBeInTheDocument()
    expect(within(advisorRow).queryByRole('button')).toBeNull()

    await user.click(screen.getByRole('textbox', { name: i18n.t('customerDetail.overview.fields.roles') }))
    const listbox = await screen.findByRole('listbox')
    const customerOption = within(listbox).getByRole('option', { name: role('customer') })
    expect(customerOption).toHaveAttribute('data-combobox-disabled')
    // The supplier role stays theirs to give.
    await user.click(within(listbox).getByRole('option', { name: role('supplier') }))
    await waitFor(() => expect(backend.callsTo(/^\/customers\/c1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/^\/customers\/c1$/, 'PATCH')[0].body).toEqual({ roles: ['customer', 'supplier'] })
  })

  it('creates a supplier: the customer role is shown, not chosen, and no status is sent', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      inventorySession(),
      { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
    ])
    const created = vi.fn()
    const { container } = renderWithProviders(<CustomerCreateFlow onSuccess={created} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
    await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
    await waitFor(() => expect(screen.queryByText(i18n.t('customerCreate.fields.lifecycleStatus'))).toBeNull())

    await user.type(container.querySelector<HTMLInputElement>('input[data-path="firstName"]')!, 'Anna')
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('customerDetail.contactPoints.addEmail'), 'i') }))
    await user.type(container.querySelector<HTMLInputElement>('input[type="email"]')!, 'anna@example.ch')
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.contactPoints.save') }))
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.submit') }))

    await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
    const body = backend.callsTo(/^\/customers$/, 'POST')[0].body as Record<string, unknown>
    expect(body.roles).toEqual(['supplier'])
    expect(body).not.toHaveProperty('lifecycleStatus')
  })
})

describe('the row menu without the customer role\'s write (KAN-464 Q3)', () => {
  const t = (key: string) => key
  const handlers = {
    onNewOffer: vi.fn(),
    onNewContract: vi.fn(),
    onCopyCustomerNumber: vi.fn(),
    onToggleDoNotContact: vi.fn(),
    onManageCreditBlock: vi.fn(),
    onMergeInto: vi.fn(),
  }
  const base = { lifecycleStatus: 'active' as const, creditBlock: false, creditBlockReason: null }
  const menu = (c: Parameters<typeof buildCustomerRowMenu>[1], mayWriteCustomerRole: boolean) =>
    buildCustomerRowMenu(t, c, handlers, { mayWriteCustomerRole })
  const labels = (m: ReturnType<typeof menu>) => [...(m.overflow.edit ?? []), ...(m.overflow.createFrom ?? [])].map((a) => a.label)

  it('offers no credit block and no merge of a customer; do-not-contact and merging suppliers stay', () => {
    const customerMenu = labels(menu({ ...base, roles: ['customer'] }, false))
    expect(customerMenu).not.toContain('customerRowMenu.setCreditBlock')
    expect(customerMenu).not.toContain('customerRowMenu.mergeInto')
    expect(customerMenu).toContain('customerRowMenu.setDoNotContact')
    expect(labels(menu({ ...base, creditBlock: true, roles: ['customer'] }, false))).not.toContain('customerRowMenu.removeCreditBlock')
    expect(labels(menu({ ...base, roles: ['supplier'] }, false))).toContain('customerRowMenu.mergeInto')

    const salesMenu = labels(menu({ ...base, roles: ['customer'] }, true))
    expect(salesMenu).toContain('customerRowMenu.setCreditBlock')
    expect(salesMenu).toContain('customerRowMenu.mergeInto')
  })
})
