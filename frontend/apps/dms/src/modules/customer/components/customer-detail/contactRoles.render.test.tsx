// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { customer } from '../../../../test/fixtures'
import type { CustomerRead } from '../../../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'
import { CustomerCreateFlow } from '../CustomerCreateFlow'
import { buildCustomerRowMenu } from '../customerRowMenu'
import { applyCustomerFilters } from '../../customerOptions'

// KAN-464 PR-1 — a contact holds roles (Kunde, Lieferant). Q1 (Anto,
// 2026-11-14, A): status, advisor and credit block are the customer role's;
// a contact without it shows none of them, only do-not-contact.

function routes(overrides: Partial<CustomerRead> = {}) {
  return [
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

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' },
  )
}

const role = (code: string) => i18n.t(`customerEnums.role.${code}`)
const lifecycle = (code: string) => i18n.t(`customerEnums.lifecycleStatus.${code}`)

describe('the 360 shows the contact roles (KAN-464)', () => {
  it('a customer shows its role badge, its status and its advisor', async () => {
    installFakeBackend(routes({ roles: ['customer'], lifecycleStatus: 'active', advisorId: 'user-7', advisorLabel: 'Rey Ortiz' }))
    renderDetail()
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.relationship'))
    expect(screen.getAllByText(role('customer')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(lifecycle('active')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(i18n.t('customerDetail.overview.fields.advisor')).length).toBeGreaterThan(0)
    expect(screen.queryByText(i18n.t('customerDetail.overview.customerRoleFieldsHint'))).toBeNull()
  })

  it('a supplier-only contact shows no status, no advisor — only do-not-contact', async () => {
    installFakeBackend(routes({ roles: ['supplier'], lifecycleStatus: 'active', advisorId: null, advisorLabel: null }))
    renderDetail()
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.relationship'))
    expect(screen.getAllByText(role('supplier')).length).toBeGreaterThan(0)
    // «Aktiv» is the stored neutral value, not a status of this contact.
    expect(screen.queryByText(lifecycle('active'))).toBeNull()
    expect(screen.queryByText(i18n.t('customerDetail.overview.fields.advisor'))).toBeNull()
    expect(screen.getByText(i18n.t('customerDetail.overview.customerRoleFieldsHint'))).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: lifecycle('do_not_contact') })).not.toBeChecked()
  })

  it('a supplier marked do-not-contact shows it in the header', async () => {
    installFakeBackend(routes({ roles: ['supplier'], lifecycleStatus: 'do_not_contact' }))
    renderDetail()
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.relationship'))
    expect(screen.getAllByText(lifecycle('do_not_contact')).length).toBeGreaterThan(0)
  })
})

describe('taking the customer role away is confirmed first (KAN-464)', () => {
  it('opens a confirmation and sends the PATCH only after it', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      ...routes({ roles: ['customer', 'supplier'], lifecycleStatus: 'active' }),
      { method: 'PATCH', match: /^\/customers\/c1$/, handler: (req) => customer({ id: 'c1', ...(req.body as object) }) },
    ])
    renderDetail()
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.relationship'))
    await user.click(screen.getByRole('textbox', { name: i18n.t('customerDetail.overview.fields.roles') }))
    const listbox = await screen.findByRole('listbox')
    await user.click(within(listbox).getByRole('option', { name: role('customer') }))

    const dialog = await screen.findByRole('dialog', { name: i18n.t('customerDetail.overview.removeCustomerRole.title') })
    expect(backend.callsTo(/^\/customers\/c1$/, 'PATCH')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('customerDetail.overview.removeCustomerRole.confirm') }))
    await waitFor(() => expect(backend.callsTo(/^\/customers\/c1$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/^\/customers\/c1$/, 'PATCH')[0].body).toEqual({ roles: ['supplier'] })
  })
})

describe('the row menu offers a credit block only to the customer role (KAN-464)', () => {
  const t = (key: string) => key
  const handlers = {
    onNewOffer: vi.fn(),
    onNewContract: vi.fn(),
    onCopyCustomerNumber: vi.fn(),
    onToggleDoNotContact: vi.fn(),
    onManageCreditBlock: vi.fn(),
  }
  const labels = (c: Parameters<typeof buildCustomerRowMenu>[1]) =>
    (buildCustomerRowMenu(t, c, handlers).overflow.edit ?? []).map((a) => a.label)
  const base = { lifecycleStatus: 'active' as const, creditBlock: false, creditBlockReason: null }

  it('a supplier is offered none; a customer and a standing block are', () => {
    expect(labels({ ...base, roles: ['supplier'] })).not.toContain('customerRowMenu.setCreditBlock')
    expect(labels({ ...base, roles: ['customer', 'supplier'] })).toContain('customerRowMenu.setCreditBlock')
    expect(labels({ ...base, creditBlock: true, roles: ['supplier'] })).toContain('customerRowMenu.removeCreditBlock')
  })
})

describe('the list sends the role filter (KAN-464)', () => {
  it('«ist» and «ist nicht» become role and roleNot', () => {
    const fields = [{ id: 'roles', label: 'Rollen', type: 'select' as const, param: 'role', options: [] }]
    const params = new URLSearchParams()
    applyCustomerFilters(
      params,
      [
        { id: 'a', fieldId: 'roles', type: 'select', condition: 'is', value: 'supplier' },
        { id: 'b', fieldId: 'roles', type: 'select', condition: 'isNot', value: 'customer' },
      ],
      fields,
    )
    expect(params.get('role')).toBe('supplier')
    expect(params.get('roleNot')).toBe('customer')
  })
})

describe('creating a contact asks for its roles (KAN-464)', () => {
  it('a new contact is a customer unless the user chooses otherwise, and a supplier-only one sends no status', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
    ])
    const created = vi.fn()
    const { container } = renderWithProviders(<CustomerCreateFlow onSuccess={created} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
    await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
    expect(screen.getByText(i18n.t('customerCreate.fields.lifecycleStatus'))).toBeInTheDocument()

    // Swap the customer role for the supplier role.
    const rolesInput = screen.getByRole('textbox', { name: i18n.t('customerCreate.fields.roles') })
    await user.click(rolesInput)
    const listbox = await screen.findByRole('listbox')
    await user.click(within(listbox).getByRole('option', { name: role('supplier') }))
    await user.click(within(listbox).getByRole('option', { name: role('customer') }))
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

// KAN-465: the purchase dialog opens the one create dialog with the supplier role preset.
describe('a create that presets its roles (KAN-465)', () => {
  it('starts with the preset roles and sends them, so a new supplier has no status', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
    ])
    const created = vi.fn()
    const { container } = renderWithProviders(
      <CustomerCreateFlow onSuccess={created} onCancel={() => {}} initialRoles={['supplier']} />,
    )
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
    await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
    expect(screen.queryByText(i18n.t('customerCreate.fields.lifecycleStatus'))).toBeNull()

    await user.type(container.querySelector<HTMLInputElement>('input[data-path="firstName"]')!, 'Hans')
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('customerDetail.contactPoints.addEmail'), 'i') }))
    await user.type(container.querySelector<HTMLInputElement>('input[type="email"]')!, 'hans@example.ch')
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.contactPoints.save') }))
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.submit') }))

    await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
    expect((backend.callsTo(/^\/customers$/, 'POST')[0].body as Record<string, unknown>).roles).toEqual(['supplier'])
  })
})
