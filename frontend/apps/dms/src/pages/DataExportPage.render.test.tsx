// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { DataExportPage } from './DataExportPage'

// KAN-218 — Dealer Administration › Datenexport (FR-A-44; Anto, 2026-11-13):
// the manager requests a full export, sees it produced, downloads it for
// seven days and confirms the receipt offboarding needs; a refusal is said in
// the UI language; a role without the row reads that the manager exports.

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

function exportRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'exp-1',
    dealershipId: 'dealership-1',
    kind: 'self_service',
    status: 'ready',
    requestedBy: 'user-1',
    reason: null,
    createdAt: '2026-11-10T08:00:00Z',
    producedAt: '2026-11-10T08:01:00Z',
    byteSize: 2_400_000,
    downloadExpiresAt: '2026-11-17T08:01:00Z',
    acknowledgedAt: null,
    acknowledgedBy: null,
    expiredAt: null,
    failureReason: null,
    version: 2,
    ...overrides,
  }
}

function install(items = [exportRow()], extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    { method: 'GET', match: /^\/dealerships\/dealership-1\/exports$/, handler: () => ({ items, nextCursor: null }) },
  ])
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getAllByRole('button', { name: i18n.t('common.rowActionsLabel') })[0])
  return within(await screen.findByRole('menu'))
}

describe('DataExportPage (KAN-218)', () => {
  it('lists the exports with their state and says what the export holds and for how long', async () => {
    install([exportRow(), exportRow({ id: 'exp-2', status: 'expired', byteSize: null, createdAt: '2026-10-01T08:00:00Z' })])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    expect(await screen.findByText(i18n.t('dataExport.status.ready'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('dataExport.status.expired'))).toBeInTheDocument()
    expect(screen.getByText('2.3 MB')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('dataExport.contents.documents'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('dataExport.window'))).toBeInTheDocument()
  })

  it('the manager requests an export through the POST', async () => {
    const user = userEvent.setup()
    const backend = install([], [{ method: 'POST', match: /^\/dealerships\/dealership-1\/exports$/, handler: () => status(202, exportRow({ status: 'requested' })) }])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    await user.click(await screen.findByRole('button', { name: i18n.t('dataExport.request') }))
    expect(await screen.findByText(i18n.t('dataExport.requested'))).toBeInTheDocument()
    expect(backend.callsTo(/\/dealerships\/dealership-1\/exports$/, 'POST')).toHaveLength(1)
  })

  it('confirms the receipt with the version it read', async () => {
    const user = userEvent.setup()
    const backend = install([exportRow()], [
      {
        method: 'POST',
        match: /^\/dealerships\/dealership-1\/exports\/exp-1\/acknowledge$/,
        handler: () => exportRow({ acknowledgedAt: '2026-11-11T09:00:00Z', version: 3 }),
      },
    ])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    await screen.findByText(i18n.t('dataExport.status.ready'))
    const menu = await openMenu(user)
    await user.click(menu.getByText(i18n.t('dataExport.rowMenu.acknowledge')))
    expect(await screen.findByText(i18n.t('dataExport.acknowledged'))).toBeInTheDocument()
    const calls = backend.callsTo(/\/exports\/exp-1\/acknowledge$/, 'POST')
    expect(calls).toHaveLength(1)
    expect(calls[0].headers.get('If-Match')).toBe('2')
  })

  it('an export not ready yet offers no download, saying why', async () => {
    const user = userEvent.setup()
    install([exportRow({ status: 'requested', producedAt: null, byteSize: null, downloadExpiresAt: null })])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    await screen.findByText(i18n.t('dataExport.status.requested'))
    const menu = await openMenu(user)
    const item = menu.getByText(i18n.t('dataExport.rowMenu.download')).closest('button')
    expect(item).toBeDisabled()
  })

  it('says a refusal in the UI language', async () => {
    const user = userEvent.setup()
    install([], [
      {
        method: 'POST',
        match: /^\/dealerships\/dealership-1\/exports$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'English', details: { reason: 'dealership_offboarded' } } }),
      },
    ])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    await user.click(await screen.findByRole('button', { name: i18n.t('dataExport.request') }))
    expect(await screen.findByText(i18n.t('dataExport.refusals.dealership_offboarded'))).toBeInTheDocument()
  })

  it('a role without the row reads that the management exports', async () => {
    install([], [
      {
        method: 'GET',
        match: /\/auth\/me$/,
        handler: () => ({
          user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Test', lastName: 'Sales', email: 's@example.ch', accessRoles: ['sales'], isDealerManager: false, status: 'active', version: 1 },
          activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' },
          memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG', dealerGroupId: 'group-1' }],
          entitlements: ['sales'],
          writableCapabilities: [],
        }),
      },
    ])
    renderWithProviders(<DataExportPage />, { route: '/administration/export' })
    expect(await screen.findByText(i18n.t('dataExport.readOnly'))).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: i18n.t('dataExport.request') })).not.toBeInTheDocument())
  })
})
