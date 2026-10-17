// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomersListPage } from './CustomersListPage'

// jsdom has no layout: render every row (the shim the columns test uses).
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

// KAN-333 — the birth date, customer-since and next-follow-up columns are
// date-only values; a timestamp column (created) keeps formatDate.
describe('CustomersListPage — date-only columns keep their day west of Greenwich (KAN-333)', () => {
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders each date-only column as the recorded day', async () => {
    installFakeBackend([
      {
        match: /^\/customers$/,
        handler: () =>
          customerPage([
            customer({
              id: 'c1', customerNumber: 'K-3001', birthDate: '1980-05-01', customerSince: '2020-01-15',
              nextFollowUp: '2026-11-01',
            }),
          ]),
      },
    ])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })
    await screen.findByText('K-3001')

    await userEvent.click(screen.getByRole('button', { name: i18n.t('common.columnPanel.trigger') }))
    for (const key of ['birthDate', 'customerSince', 'nextFollowUp']) {
      const label = i18n.t(`customersList.columns.${key}`)
      await userEvent.click(screen.getByRole('checkbox', { name: new RegExp(`^${label}$`, 'i') }))
    }

    expect(screen.getByText('01.05.1980')).toBeInTheDocument()
    expect(screen.getByText('15.01.2020')).toBeInTheDocument()
    expect(screen.getByText('01.11.2026')).toBeInTheDocument()
    expect(screen.queryByText('30.04.1980')).not.toBeInTheDocument()
    expect(screen.queryByText('14.01.2020')).not.toBeInTheDocument()
    expect(screen.queryByText('31.10.2026')).not.toBeInTheDocument()
  })
})
