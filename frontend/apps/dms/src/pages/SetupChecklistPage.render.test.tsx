// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { SetupChecklistPage } from './SetupChecklistPage'

// KAN-214 — Dealer Administration › Setup: the derived checklist with what
// each item blocks and where to fix it; «Betrieb aktivieren» with the ToS /
// DPA acceptance and the number-range freeze warning, accepted before the
// activation, which goes under If-Match.

const ITEMS = ['branding', 'vat_and_rounding', 'bank_account', 'number_ranges', 'location_opening_hours', 'users']
const LINKS: Record<string, string> = {
  branding: '/administration/branding',
  vat_and_rounding: '/administration/business',
  bank_account: '/administration/business',
  number_ranges: '/administration/number-ranges',
  location_opening_hours: '/administration/locations',
  users: '/administration/users',
}

const summary = (documentType: 'tos' | 'dpa') => ({
  id: `doc-${documentType}`,
  documentType,
  version: '0.9',
  language: 'de',
  effectiveDate: '2026-11-14',
  title: documentType === 'tos' ? 'Nutzungsbedingungen (Entwurf)' : 'Auftragsverarbeitungsvertrag (Entwurf)',
  draft: true,
})

const checklist = (overrides: Record<string, unknown> = {}, open: string[] = []) => ({
  dealershipId: 'dealership-1',
  status: 'pending_onboarding',
  complete: open.length === 0,
  items: ITEMS.map((code) => ({ code, complete: !open.includes(code), blocks: null, link: LINKS[code] })),
  legalAcceptance: { documents: [summary('dpa'), summary('tos')], dueBy: '2026-12-14', held: false },
  legalDocumentsFinal: true,
  mayActivate: false,
  numberRangesConfirmedAt: null,
  version: 7,
  ...overrides,
})

function install(body: unknown, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships\/dealership-1\/setup-checklist$/, handler: () => body },
    {
      method: 'GET',
      match: /^\/legal-documents\/current$/,
      handler: () => ({
        items: (['dpa', 'tos'] as const).map((type) => ({
          ...summary(type),
          bodyMarkdown: `> **Entwurf 0.9**\n\n## 1. Gegenstand\n\nText ${type}.`,
          acceptanceGraceDays: 30,
          publishedAt: '2026-11-14T00:00:00Z',
        })),
      }),
    },
  ])
}

afterEach(() => {
  cleanup()
})

describe('SetupChecklistPage (KAN-214)', () => {
  it('lists every item with its state and a link to the screen that completes it', async () => {
    install(checklist({}, ['bank_account', 'number_ranges']))
    renderWithProviders(<SetupChecklistPage />, { route: '/administration/setup' })

    const table = await screen.findByRole('table', { name: i18n.t('setup.itemsTitle') })
    const bank = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('setup.items.bank_account.title')) })
    expect(within(bank).getByText(i18n.t('setup.missing'))).toBeInTheDocument()
    expect(within(bank).getByRole('link', { name: i18n.t('setup.open') })).toHaveAttribute('href', '/administration/business')
    const branding = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('setup.items.branding.title')) })
    expect(within(branding).getByText(i18n.t('setup.complete'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('setup.pendingNotice'))).toBeInTheDocument()
    // Incomplete: activation is not offered yet.
    expect(screen.getByRole('button', { name: i18n.t('setup.activate.button') })).toBeDisabled()
    expect(screen.getByText(i18n.t('setup.activate.incomplete'))).toBeInTheDocument()
  })

  it('activation needs both documents ticked, accepts them, then activates under If-Match', async () => {
    const user = userEvent.setup()
    const backend = install(checklist(), [
      { method: 'POST', match: /\/legal-acceptances$/, handler: () => status(201, { id: 'acc' }) },
      { method: 'POST', match: /\/activation$/, handler: () => ({ dealershipId: 'dealership-1', status: 'active', activatedAt: '2026-11-14T10:00:00Z', version: 8 }) },
    ])
    renderWithProviders(<SetupChecklistPage />, { route: '/administration/setup' })

    await user.click(await screen.findByRole('button', { name: i18n.t('setup.activate.button') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.activate.title') })
    expect(within(dialog).getByText(i18n.t('setup.activate.freezeWarning'))).toBeInTheDocument()
    const submit = within(dialog).getByRole('button', { name: i18n.t('setup.activate.submit') })
    const boxes = await within(dialog).findAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    expect(submit).toBeDisabled()
    await user.click(boxes[0])
    expect(submit).toBeDisabled()
    await user.click(boxes[1])
    await user.click(submit)

    await waitFor(() => expect(backend.calls.some((c) => c.method === 'POST' && c.pathname.endsWith('/activation'))).toBe(true))
    const posts = backend.calls.filter((c) => c.method === 'POST')
    expect(posts.map((c) => c.pathname)).toEqual([
      '/dealerships/dealership-1/legal-acceptances',
      '/dealerships/dealership-1/legal-acceptances',
      '/dealerships/dealership-1/activation',
    ])
    expect(posts.slice(0, 2).map((c) => (c.body as { legalDocumentId: string }).legalDocumentId).sort()).toEqual(['doc-dpa', 'doc-tos'])
    expect(posts[2].headers.get('if-match')).toBe('7')
  })

  it('says why the server refused the activation, in the UI language', async () => {
    const user = userEvent.setup()
    install(checklist(), [
      { method: 'POST', match: /\/legal-acceptances$/, handler: () => status(201, { id: 'acc' }) },
      {
        method: 'POST',
        match: /\/activation$/,
        handler: () =>
          status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'setup_checklist_incomplete', missing: ['users'] } } }),
      },
    ])
    renderWithProviders(<SetupChecklistPage />, { route: '/administration/setup' })
    await user.click(await screen.findByRole('button', { name: i18n.t('setup.activate.button') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.activate.title') })
    for (const box of await within(dialog).findAllByRole('checkbox')) await user.click(box)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('setup.activate.submit') }))
    expect(await within(dialog).findByText(i18n.t('setup.refusals.setup_checklist_incomplete'))).toBeInTheDocument()
  })

  it('production waits for the final texts: activation is not offered on a draft', async () => {
    install(checklist({ legalDocumentsFinal: false }))
    renderWithProviders(<SetupChecklistPage />, { route: '/administration/setup' })
    expect(await screen.findByText(i18n.t('setup.legal.notFinal'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('setup.activate.button') })).toBeDisabled()
  })

  it('an active Betrieb shows what each item blocks after activation, and no activation', async () => {
    install(
      checklist(
        {
          status: 'active',
          items: [{ code: 'location_opening_hours', complete: false, blocks: 'marketplace_publishing', link: '/administration/locations' }],
          legalAcceptance: { documents: [], dueBy: null, held: false },
        },
        ['location_opening_hours'],
      ),
    )
    renderWithProviders(<SetupChecklistPage />, { route: '/administration/setup' })
    expect(await screen.findByText(i18n.t('setup.blocks.marketplace_publishing'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('setup.activeNotice'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('setup.activate.button') })).not.toBeInTheDocument()
  })
})
