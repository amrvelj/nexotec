// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRequest, type FakeRoute } from '../test/fakeBackend'
import { PlatformOffboardingPage } from './PlatformOffboardingPage'

// KAN-218 — Platform Administration › Offboarding (FR-A-45; Anto,
// 2026-11-13): staff see why a Betrieb cannot be offboarded yet, offboard it
// through a confirmation once its manager acknowledged a current export, and
// produce an archive export with a reason afterwards; the manager's own
// export never downloads here.

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

afterEach(() => cleanup())

function state(overrides: Record<string, unknown> = {}) {
  return {
    dealershipId: 'd-1',
    legalName: 'Garage Nord AG',
    status: 'active',
    version: 7,
    offboardedAt: null,
    retentionUntil: null,
    lastWriteAt: '2026-11-10T08:00:00Z',
    acknowledgedExportId: 'exp-1',
    acknowledgedExportProducedAt: '2026-11-11T08:00:00Z',
    refusal: null,
    ...overrides,
  }
}

const OWN_EXPORT = {
  id: 'exp-1',
  dealershipId: 'd-1',
  kind: 'self_service',
  status: 'ready',
  requestedBy: 'user-1',
  reason: null,
  createdAt: '2026-11-11T07:59:00Z',
  producedAt: '2026-11-11T08:00:00Z',
  byteSize: 1_200_000,
  downloadExpiresAt: '2026-11-18T08:00:00Z',
  acknowledgedAt: '2026-11-11T09:00:00Z',
  acknowledgedBy: 'user-1',
  expiredAt: null,
  failureReason: null,
  version: 3,
}

function install(current: Record<string, unknown>, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships$/, handler: () => ({ items: [{ id: 'd-1', legalName: 'Garage Nord AG', tradeName: null }], nextCursor: null }) },
    { method: 'GET', match: /^\/platform\/dealerships\/d-1\/offboarding$/, handler: () => current },
    { method: 'GET', match: /^\/platform\/dealerships\/d-1\/exports$/, handler: () => ({ items: [OWN_EXPORT], nextCursor: null }) },
  ])
}

const ROUTE = '/platform/offboarding?dealershipId=d-1'

describe('PlatformOffboardingPage (KAN-218)', () => {
  it('says why a Betrieb cannot be offboarded yet and holds the action back', async () => {
    install(state({ refusal: 'export_out_of_date' }))
    renderWithProviders(<PlatformOffboardingPage />, { route: ROUTE })
    expect(await screen.findByText(i18n.t('offboarding.refusal.export_out_of_date'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('offboarding.offboard') })).toBeDisabled()
  })

  it('offboards through the confirmation, with the version it read', async () => {
    const user = userEvent.setup()
    const backend = install(state(), [
      {
        method: 'POST',
        match: /^\/platform\/dealerships\/d-1\/offboard$/,
        handler: () => state({ status: 'offboarded', offboardedAt: '2026-11-14T10:00:00Z', retentionUntil: '2036-11-14' }),
      },
    ])
    renderWithProviders(<PlatformOffboardingPage />, { route: ROUTE })
    await user.click(await screen.findByRole('button', { name: i18n.t('offboarding.offboard') }))
    const dialog = within(await screen.findByRole('dialog'))
    const submit = dialog.getByRole('button', { name: i18n.t('offboarding.confirm.submit') })
    expect(submit).toBeDisabled()
    await user.type(dialog.getByLabelText(i18n.t('offboarding.confirm.reason')), 'Vertrag gekündigt')
    await user.click(dialog.getByLabelText(i18n.t('offboarding.confirm.check')))
    await user.click(submit)
    expect(await screen.findByText(i18n.t('offboarding.done'))).toBeInTheDocument()
    const calls = backend.callsTo(/\/offboard$/, 'POST')
    expect(calls).toHaveLength(1)
    expect(calls[0].headers.get('If-Match')).toBe('7')
    expect(calls[0].body).toEqual({ confirmation: true, reason: 'Vertrag gekündigt' })
  })

  it('an offboarded Betrieb takes an archive export with a reason; the manager’s own export stays theirs', async () => {
    const user = userEvent.setup()
    const backend = install(state({ status: 'offboarded', offboardedAt: '2026-11-14T10:00:00Z', retentionUntil: '2036-11-14' }), [
      {
        method: 'POST',
        match: /^\/platform\/dealerships\/d-1\/exports$/,
        handler: (req: FakeRequest) => status(202, { ...OWN_EXPORT, id: 'exp-2', kind: 'archive', status: 'requested', reason: (req.body as { reason: string }).reason }),
      },
    ])
    renderWithProviders(<PlatformOffboardingPage />, { route: ROUTE })
    expect(await screen.findByText(i18n.t('offboarding.fields.retentionUntil', { day: '14.11.2036' }))).toBeInTheDocument()
    const request = screen.getByRole('button', { name: i18n.t('offboarding.archive.request') })
    expect(request).toBeDisabled()
    await user.type(screen.getByLabelText(i18n.t('offboarding.archive.reason')), 'Ehemaliger Geschäftsführer, Ticket 4711')
    await user.click(request)
    expect(await screen.findByText(i18n.t('offboarding.archive.requested'))).toBeInTheDocument()
    expect(backend.callsTo(/\/platform\/dealerships\/d-1\/exports$/, 'POST')[0].body).toEqual({ reason: 'Ehemaliger Geschäftsführer, Ticket 4711' })

    await user.click(screen.getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })[0])
    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByText(i18n.t('dataExport.rowMenu.download')).closest('button')).toBeDisabled()
  })

  it('tells anyone but staff the page is staff’s', async () => {
    install(state(), [
      { method: 'GET', match: /^\/dealerships$/, handler: () => status(403, { error: { code: 'forbidden', message: 'English', details: null } }) },
    ])
    renderWithProviders(<PlatformOffboardingPage />, { route: ROUTE })
    expect(await screen.findByText(i18n.t('offboarding.forbidden.title'))).toBeInTheDocument()
  })
})
