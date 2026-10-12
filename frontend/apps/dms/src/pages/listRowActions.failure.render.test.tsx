// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import type { ValuationRead } from '../api/types'
import { CustomersListPage } from './CustomersListPage'
import { ValuationsListPage } from './ValuationsListPage'

// KAN-273 — a write started from a list's row menu says so when it fails,
// above the grid, and its retry from the same row carries the same key.
//
// jsdom has no layout, so TanStack Virtual windows down to zero rows and no
// row menu would ever render. As in the ui-kit's DataGrid.rowActivate test,
// the virtualizer is replaced by "render every row".
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

const refused = (message: string) => status(503, { error: { code: 'unavailable', message, details: null } })

/** The first call fails with `failure()`; every later one gets `ok()`. */
function failOnce(failure: () => unknown, ok: () => unknown) {
  let failed = false
  return () => {
    if (failed) return ok()
    failed = true
    return failure()
  }
}

const keys = (backend: ReturnType<typeof installFakeBackend>, pattern: RegExp) =>
  backend.callsTo(pattern, 'POST').map((call) => call.headers.get('Idempotency-Key'))

async function openRowMenu(user: ReturnType<typeof userEvent.setup>) {
  const [menuButton] = await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
  await user.click(menuButton)
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('CustomersListPage — a failed create from the row menu says so (KAN-273)', () => {
  const install = (route: { offers?: () => unknown; contracts?: () => unknown }) =>
    installFakeBackend([
      { method: 'GET', match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1', lastName: 'Aebi' })]) },
      ...(route.offers ? [{ method: 'POST', match: /^\/sales\/offers$/, handler: route.offers }] : []),
      { method: 'PATCH', match: /^\/sales\/offers\/o9$/, handler: () => ({ id: 'o9', version: 2 }) },
      ...(route.contracts ? [{ method: 'POST', match: /^\/sales\/contracts$/, handler: route.contracts }] : []),
    ])

  it('a refused new offer shows the server message, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    const backend = install({ offers: failOnce(() => refused('Number range unavailable'), () => status(201, { id: 'o9', version: 1 })) })
    renderWithProviders(<CustomersListPage />, { route: '/customers' })
    const newOffer = async () => {
      await openRowMenu(user)
      await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.newOffer')) }))
    }

    await newOffer()
    expect((await screen.findByRole('alert')).textContent).toBe('Number range unavailable')

    await newOffer()
    await waitFor(() => expect(keys(backend, /^\/sales\/offers$/)).toHaveLength(2))
    await waitFor(() => expect(backend.callsTo(/^\/sales\/offers\/o9$/, 'PATCH')).toHaveLength(1))

    const [failedKey, retryKey] = keys(backend, /^\/sales\/offers$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a dropped new contract shows a translated message, and the retry reuses the key', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    const backend = install({
      contracts: failOnce(
        () => {
          throw new TypeError('Failed to fetch')
        },
        () => status(201, { id: 'k9', version: 1 }),
      ),
    })
    renderWithProviders(<CustomersListPage />, { route: '/customers' })
    const newContract = async () => {
      await openRowMenu(user)
      await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.newContract')) }))
    }

    await newContract()
    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('customersList.createContractError'))

    await newContract()
    await waitFor(() => expect(keys(backend, /^\/sales\/contracts$/)).toHaveLength(2))

    const [failedKey, retryKey] = keys(backend, /^\/sales\/contracts$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})

// KAN-308 — "Nicht kontaktieren" from the row menu: a refused PATCH is said
// above the grid (the server's message, or the translated fallback when the
// request never reached it), and a version conflict refetches the grid.
describe('CustomersListPage — a failed do-not-contact toggle says so (KAN-308)', () => {
  const install = (patch: () => unknown) =>
    installFakeBackend([
      { method: 'GET', match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1', lastName: 'Aebi' })]) },
      { method: 'PATCH', match: /^\/customers\/c1$/, handler: patch },
    ])

  const toggle = async (user: ReturnType<typeof userEvent.setup>) => {
    await openRowMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('customerRowMenu.setDoNotContact')) }))
  }

  it('a version conflict shows the server message and refetches the grid', async () => {
    const user = userEvent.setup()
    const backend = install(() =>
      status(409, { error: { code: 'conflict', message: 'Customer was changed by someone else.', details: { currentVersion: 2 } } }),
    )
    renderWithProviders(<CustomersListPage />, { route: '/customers' })
    await screen.findAllByRole('button', { name: i18n.t('common.rowActionsLabel') })
    const listCallsBefore = backend.callsTo(/^\/customers$/, 'GET').length

    await toggle(user)

    expect((await screen.findByRole('alert')).textContent).toBe('Customer was changed by someone else.')
    await waitFor(() => expect(backend.callsTo(/^\/customers$/, 'GET').length).toBeGreaterThan(listCallsBefore))
  })

  it('a request that never reached the server shows the translated fallback', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    install(() => {
      throw new TypeError('Failed to fetch')
    })
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await toggle(user)

    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('customersList.doNotContactError'))
  })
})

describe('ValuationsListPage — a failed «Als verwendet markieren» says so (KAN-273)', () => {
  const VALUATION = {
    id: 'val-1', valuationNumber: 'B-000001', status: 'valid', source: 'manual', hasSignedContract: true,
    customerId: null, customerLabel: null, vehicleId: null, vehicleMake: 'VW', vehicleModel: 'Golf', vehicleTrim: null,
    vehicleVin: null, finalOffer: '12000.00', deductions: [], version: 2, validFrom: '2026-10-01',
    validUntil: '2026-10-31', createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
  } as unknown as ValuationRead

  it('a dropped stamp shows a translated message above the grid, and the retry reuses the key', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    const backend = installFakeBackend([
      {
        method: 'GET',
        match: /^\/valuations$/,
        handler: () => ({ items: [VALUATION], nextCursor: null, total: 1, totalIsEstimate: false }),
      },
      {
        method: 'POST',
        match: /^\/valuations\/val-1\/mark-used$/,
        handler: failOnce(
          () => {
            throw new TypeError('Failed to fetch')
          },
          () => ({ ...VALUATION, status: 'used', version: 3 }),
        ),
      },
    ])
    renderWithProviders(<ValuationsListPage />, { route: '/valuations' })
    const markUsed = async () => {
      await openRowMenu(user)
      await user.click(await screen.findByRole('menuitem', { name: new RegExp(i18n.t('valuationDetail.actions.markUsed')) }))
    }

    await markUsed()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(i18n.t('valuationsList.markUsedError'))
    // Above the grid, not inside it.
    expect(within(screen.getByRole('table')).queryByRole('alert')).toBeNull()

    await markUsed()
    await waitFor(() => expect(keys(backend, /\/mark-used$/)).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    const [failedKey, retryKey] = keys(backend, /\/mark-used$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})
