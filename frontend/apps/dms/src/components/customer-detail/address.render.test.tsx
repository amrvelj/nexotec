// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer, customerAddress } from '../../test/fixtures'
import type { CustomerAddressRead } from '../../api/types'
import { CustomerDetailPage } from '../../pages/CustomerDetailPage'

// KAN-30 (ADR-067): the customer's address is a /customers/{id}/addresses
// child row, not a flat field. The read half must render it correctly
// (never "undefined undefined, undefined undefined") and label the
// server-derived canton as derived; the write half must actually save,
// through the real endpoint, not a silently-dropped PATCH /customers/{id}.

function infraRoutes(address: CustomerAddressRead | null) {
  return [
    { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/transactions$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', address }) },
  ]
}

// Several other Overview fields (nationality, source, ...) are nullable
// and share the same "Not set" empty-state label (InlineEditField's
// emptyLabel), so a plain getAllByText for it is ambiguous across the
// whole page — scope to the Address OverviewCard specifically. The card
// title's own text node is two levels up from the card's outer container
// (title -> header row -> card).
function addressCard(): HTMLElement {
  // In German the card title ("Adresse") and the field label inside it
  // are the same string, so text alone is ambiguous — the card title is
  // the <div>, the field label is a <span> (OverviewCard/KeyValueRow).
  const title = screen.getAllByText(i18n.t('customerDetail.overview.cards.address')).find((el) => el.tagName === 'DIV')!
  return title.parentElement!.parentElement as HTMLElement
}

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/customers/:id" element={<CustomerDetailPage />} />
    </Routes>,
    { route: '/customers/c1' }
  )
}

describe('customer address — detail screen (ADR-067, KAN-30)', () => {
  it('renders the address line and the derived canton, never "undefined"', async () => {
    installFakeBackend(infraRoutes(customerAddress()))
    renderDetail()

    expect(await screen.findByText('Bahnhofstrasse 1, 8001 Zürich')).toBeInTheDocument()
    expect(screen.getByText('ZH')).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })

  it('renders "Not set" for a missing address, and labels canton as derived', async () => {
    installFakeBackend(infraRoutes(null))
    renderDetail()

    await screen.findAllByText(i18n.t('customerDetail.overview.cards.address'))
    // The address line and the canton row both fall back to the same
    // empty-state label — both inside the Address card.
    expect(within(addressCard()).getAllByText(i18n.t('customerDetail.overview.notSet')).length).toBe(2)
    expect(within(addressCard()).getByText(i18n.t('customerDetail.overview.fields.cantonDerived'))).toBeInTheDocument()
  })

  it('editing a missing address POSTs a new primary domicile row, and the server-derived canton comes back', async () => {
    let address: CustomerAddressRead | null = null
    const backend = installFakeBackend([
      { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', address }) },
      ...infraRoutes(null).slice(0, -1),
      {
        method: 'POST',
        match: /^\/customers\/c1\/addresses$/,
        handler: (req) => {
          address = { ...customerAddress(), ...(req.body as object), addressCanton: 'BE' }
          return address
        },
      },
    ])
    renderDetail()
    const user = userEvent.setup()

    await screen.findAllByText(i18n.t('customerDetail.overview.cards.address'))
    // The address line (AddressField's own clickable span) renders before
    // the canton row within the same card.
    await user.click(within(addressCard()).getAllByText(i18n.t('customerDetail.overview.notSet'))[0])
    await user.type(screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.street')), 'Marktgasse')
    await user.type(screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.houseNumber')), '5')
    await user.type(screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.postalCode')), '3011')
    await user.type(screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.locality')), 'Bern')
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.overview.addressForm.save') }))

    await waitFor(() => {
      const posts = backend.callsTo(/^\/customers\/c1\/addresses$/, 'POST')
      expect(posts).toHaveLength(1)
      expect(posts[0].body).toMatchObject({
        addressType: 'domicile',
        addressStreet: 'Marktgasse',
        addressHouseNumber: '5',
        addressPostalCode: '3011',
        addressLocality: 'Bern',
        isPrimary: true,
      })
    })

    // The customer refetch after invalidation picks up the row the "server"
    // just derived a canton for — this is the write-half exit criterion:
    // canton comes back populated after a save, never client-supplied.
    expect(await screen.findByText('BE')).toBeInTheDocument()
  })

  it('renders addressLine2 between the label and the street, when set', async () => {
    installFakeBackend(infraRoutes(customerAddress({ addressLine2: 'c/o Muster Treuhand AG' })))
    renderDetail()

    expect(await screen.findByText('c/o Muster Treuhand AG, Bahnhofstrasse 1, 8001 Zürich')).toBeInTheDocument()
  })

  it('editing an address round-trips addressLine2 through the form', async () => {
    const existing = customerAddress({ id: 'addr-9' })
    const backend = installFakeBackend([
      ...infraRoutes(existing),
      {
        method: 'PATCH',
        match: /^\/customers\/c1\/addresses\/addr-9$/,
        handler: (req) => ({ ...existing, ...(req.body as object) }),
      },
    ])
    renderDetail()
    const user = userEvent.setup()

    await user.click(await screen.findByText('Bahnhofstrasse 1, 8001 Zürich'))
    await user.type(screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.line2')), 'Postfach 42')
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.overview.addressForm.save') }))

    await waitFor(() => {
      expect(backend.callsTo(/^\/customers\/c1\/addresses\/addr-9$/, 'PATCH')[0].body).toMatchObject({
        addressLine2: 'Postfach 42',
      })
    })
  })

  it('editing an existing address PATCHes it by id — never a second POST', async () => {
    const existing = customerAddress({ id: 'addr-9' })
    const backend = installFakeBackend([
      ...infraRoutes(existing),
      {
        method: 'PATCH',
        match: /^\/customers\/c1\/addresses\/addr-9$/,
        handler: (req) => ({ ...existing, ...(req.body as object) }),
      },
    ])
    renderDetail()
    const user = userEvent.setup()

    await user.click(await screen.findByText('Bahnhofstrasse 1, 8001 Zürich'))
    const locality = screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.locality'))
    await user.clear(locality)
    await user.type(locality, 'Winterthur')
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.overview.addressForm.save') }))

    await waitFor(() => {
      expect(backend.callsTo(/^\/customers\/c1\/addresses\/addr-9$/, 'PATCH')).toHaveLength(1)
      expect(backend.callsTo(/^\/customers\/c1\/addresses$/, 'POST')).toHaveLength(0)
    })
    expect(backend.callsTo(/^\/customers\/c1\/addresses\/addr-9$/, 'PATCH')[0].body).toMatchObject({
      addressLocality: 'Winterthur',
    })
  })

  it('clearing every field on an existing address DELETEs it', async () => {
    const existing = customerAddress({ id: 'addr-7' })
    const backend = installFakeBackend([
      ...infraRoutes(existing),
      { method: 'DELETE', match: /^\/customers\/c1\/addresses\/addr-7$/, handler: () => ({ __status: 204 }) },
    ])
    renderDetail()
    const user = userEvent.setup()

    await user.click(await screen.findByText('Bahnhofstrasse 1, 8001 Zürich'))
    for (const key of ['street', 'houseNumber', 'postalCode', 'locality'] as const) {
      const field = screen.getByLabelText(i18n.t(`customerDetail.overview.addressForm.${key}`))
      await user.clear(field)
    }
    await user.click(screen.getByRole('button', { name: i18n.t('customerDetail.overview.addressForm.save') }))

    await waitFor(() => {
      expect(backend.callsTo(/^\/customers\/c1\/addresses\/addr-7$/, 'DELETE')).toHaveLength(1)
    })
  })
})
