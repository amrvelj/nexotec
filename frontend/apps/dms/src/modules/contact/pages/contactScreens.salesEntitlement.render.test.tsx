// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { ALL_GATED_CAPABILITIES, installFakeBackend, type FakeRoute } from '../../../test/fakeBackend'
import { contact, contactPage } from '../../../test/fixtures'
import { ContactDetailPage } from './ContactDetailPage'
import { ContactsListPage } from './ContactsListPage'

// KAN-377 (D-A-04): a Betrieb without the sales module gets 404 from every
// `/sales/*` route, so its contact screens neither read offers/contracts nor
// offer "New offer" / "New contract". Presentation only — the 404 stays the
// boundary.
//
// jsdom has no layout, so TanStack Virtual windows down to zero rows and no
// row menu would ever render; the virtualizer is replaced by "render every row".
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const WORKSHOP_ENTITLEMENTS = ['aftersales', 'parts']

const session = (entitlements: string[]): FakeRoute => ({
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: {
      id: 'user-1',
      firstName: 'Test',
      lastName: 'User',
      email: 'test@example.ch',
      accessRoles: [],
    },
    activeDealership: { id: 'd-1', legalName: 'Garage Nord AG' },
    memberships: [{ id: 'd-1', legalName: 'Garage Nord AG' }],
    entitlements,
    // Everything a screen may ask about: this file is about the entitlement,
    // not the role (KAN-464 PR-2 gates the merge of a contact on one).
    writableCapabilities: ALL_GATED_CAPABILITIES,
  }),
})

function installDetail(entitlements: string[]) {
  return installFakeBackend([
    session(entitlements),
    { match: /^\/contacts\/c1$/, handler: () => contact({ id: 'c1', contactNumber: 'K-1001', lastName: 'Aebi' }) },
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

function renderDetail(route = '/contacts/c1') {
  renderWithProviders(
    <Routes>
      <Route path="/contacts/:id" element={<ContactDetailPage />} />
    </Routes>,
    { route },
  )
}

const offersTab = () => i18n.t('contactDetail.tabs.offersContracts')
const newOffer = () => new RegExp(i18n.t('contactRowMenu.newOffer'))
const newContract = () => new RegExp(i18n.t('contactRowMenu.newContract'))
// KAN-468 removed «Edit» and its key; the label it had, in DE and EN.
const EDIT = /Bearbeiten|\bEdit\b/

describe('Contact detail for a Betrieb without sales (KAN-377)', () => {
  it('issues no /sales/* request and shows neither the tab nor a sales action', async () => {
    const user = userEvent.setup()
    const backend = installDetail(WORKSHOP_ENTITLEMENTS)
    renderDetail()

    // The header has rendered, with "Link vehicle" as its primary (KAN-468).
    expect(await screen.findByRole('button', { name: i18n.t('contactRowMenu.linkVehicle') })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/contacts\/c1\/audit-log$/)).not.toHaveLength(0))

    expect(screen.queryByRole('tab', { name: new RegExp(offersTab()) })).toBeNull()
    expect(screen.queryByRole('button', { name: newOffer() })).toBeNull()

    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contactRowMenu.mergeInto')) })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: newOffer() })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: newContract() })).toBeNull()

    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })

  it('a pasted ?tab=offersContracts opens the default tab and still calls no sales endpoint', async () => {
    const backend = installDetail(WORKSHOP_ENTITLEMENTS)
    renderDetail('/contacts/c1?tab=offersContracts')

    expect(await screen.findByRole('button', { name: i18n.t('contactRowMenu.linkVehicle') })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/contacts\/c1\/audit-log$/)).not.toHaveLength(0))
    expect(screen.queryByText(i18n.t('contactDetail.offersContracts.emptyState.title'))).toBeNull()
    expect(screen.getByRole('tab', { name: new RegExp(i18n.t('contactDetail.tabs.overview')) })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })

  it('a Betrieb with sales still reads the deals and offers both actions', async () => {
    const user = userEvent.setup()
    const backend = installDetail(['sales'])
    renderDetail()

    expect(await screen.findByRole('tab', { name: new RegExp(offersTab()) })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: newOffer() })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: newContract() })).toBeInTheDocument()
    await waitFor(() => expect(backend.callsTo(/\/sales\/offers$/, 'GET')).not.toHaveLength(0))
    await waitFor(() => expect(backend.callsTo(/\/sales\/contracts$/, 'GET')).not.toHaveLength(0))
  })
})

describe('Contact detail header (KAN-468; FR-22 amended, ADR-061)', () => {
  it('has no «Edit»: «New offer» is the primary, «Link vehicle» the alternative', async () => {
    const user = userEvent.setup()
    installDetail(['sales'])
    renderDetail()

    const primary = await screen.findByRole('button', { name: newOffer() })
    expect(primary).toHaveAttribute('data-variant', 'filled')
    expect(screen.getByRole('button', { name: i18n.t('contactRowMenu.linkVehicle') })).toHaveAttribute(
      'data-variant',
      'default',
    )
    expect(screen.queryByRole('button', { name: EDIT })).toBeNull()

    await user.click(screen.getByRole('button', { name: i18n.t('common.moreActions') }))
    expect(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contactRowMenu.mergeInto')) })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: EDIT })).toBeNull()
  })
})

describe('Contacts list row menu for a Betrieb without sales (KAN-377)', () => {
  it('offers neither "New offer" nor "New contract"', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      session(WORKSHOP_ENTITLEMENTS),
      { method: 'GET', match: /^\/contacts$/, handler: () => contactPage([contact({ id: 'c1', lastName: 'Aebi' })]) },
    ])
    renderWithProviders(<ContactsListPage />, { route: '/contacts' })

    const [menuButton] = await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    await user.click(menuButton)
    // KAN-468: the list opens the record with «Open»; there is no «Edit».
    expect(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('contactsList.rowActions.open')) })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: EDIT })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: newOffer() })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: newContract() })).toBeNull()
    expect(backend.calls.filter((call) => call.pathname.includes('/sales/'))).toEqual([])
  })
})
