// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import type { CustomerRead } from '../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'

// KAN-466 — the customer's contact groups on the 360: badges in the header,
// an inline multi-select offering the groups the Betrieb has switched on, and
// a group the Betrieb has not switched on kept, read-only, on every save.

const group = (valueCode: string, labelDe: string, enabled: boolean) => ({
  valueCode,
  labelDe,
  labelFr: labelDe,
  labelIt: labelDe,
  labelEn: labelDe,
  sortOrder: 0,
  active: true,
  enabled,
})

function routes(overrides: Partial<CustomerRead> = {}) {
  return [
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/advisor-options$/, handler: () => ({ items: [] }) },
    {
      match: /^\/dealerships\/dealership-1\/contact-groups$/,
      handler: () => ({
        items: [group('private', 'Privat', true), group('fleet', 'Flotte', true), group('insurer', 'Versicherung', false)],
      }),
    },
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', ...overrides }) },
  ]
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' },
  )
}

const fieldRow = () =>
  screen.getByText(i18n.t('customerDetail.overview.fields.contactGroups')).parentElement as HTMLElement

describe('customer 360 — contact groups (KAN-466)', () => {
  it('shows the groups as header badges, a sister Betrieb’s one read-only beside the picker', async () => {
    installFakeBackend(routes({ contactGroups: ['fleet', 'insurer'] }))
    render()
    await screen.findByText(i18n.t('customerDetail.overview.fields.contactGroups'))
    // Header and field both name them by label, never by code.
    await waitFor(() => expect(screen.getAllByText('Versicherung').length).toBeGreaterThanOrEqual(2))
    expect(screen.queryByText('insurer')).not.toBeInTheDocument()
    const readOnly = within(fieldRow()).getByTitle(i18n.t('contactGroups.field.readOnlyHint'))
    expect(within(readOnly).getByText('Versicherung')).toBeInTheDocument()
  })

  it('adding a group PATCHes the full set and keeps the read-only one', async () => {
    const backend = installFakeBackend([
      ...routes({ contactGroups: ['insurer'] }),
      { method: 'PATCH', match: /^\/customers\/c1$/, handler: (req) => customer({ id: 'c1', ...(req.body as object) }) },
    ])
    render()
    await screen.findByText(i18n.t('customerDetail.overview.fields.contactGroups'))
    const user = userEvent.setup()
    const picker = within(fieldRow()).getByRole('textbox', { name: i18n.t('customerDetail.overview.fields.contactGroups') })
    await user.click(picker)
    // Only what the Betrieb switched on is offered.
    expect(screen.queryByRole('option', { name: 'Versicherung' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('option', { name: 'Flotte' }))
    await waitFor(() => {
      const patches = backend.callsTo(/^\/customers\/c1$/, 'PATCH')
      expect(patches.at(-1)!.body).toEqual({ contactGroups: ['insurer', 'fleet'] })
    })
  })
})
