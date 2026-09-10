// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomersListPage } from './CustomersListPage'

// jsdom has no layout, so TanStack Virtual windows down to zero rows and
// the other page render tests only assert on headers. Mock it to "render
// every row" so these can assert on cell content (same shim ui-kit's
// DataGrid.rowActivate.render.test.tsx uses).
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

// The Export bulk action is verified by what it hands the (separately
// unit-tested) CSV builder — the selected rows and the visible columns —
// not by the Blob/anchor plumbing underneath.
const { exportSpy, printSpy } = vi.hoisted(() => ({ exportSpy: vi.fn(), printSpy: vi.fn() }))
vi.mock('../utils/gridExport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/gridExport')>()),
  exportRowsToCsv: exportSpy,
  printRows: printSpy,
}))

// KAN-51 — FR-17 / ADR-060: every persisted CustomerRead field is an
// available column, twelve cells visible by default (six of them until the
// KAN-50 stored fields land). ADR-058: filters and columns are one set.
// D-25: Export and Print are the bulk actions.

const columnHeader = (key: string) => i18n.t(`customersList.columns.${key}`)

function route(rows = [customer({ id: 'c1' })]) {
  return installFakeBackend([{ match: /^\/customers$/, handler: () => customerPage(rows) }])
}

describe('CustomersListPage — the column set (KAN-51 half 1)', () => {
  it('shows exactly the six default-visible cells whose fields exist today, in order', async () => {
    route()
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await screen.findByRole('columnheader', { name: new RegExp(columnHeader('name'), 'i') })

    const headers = screen
      .getAllByRole('columnheader')
      .map((h) => h.textContent?.trim())
      .filter((text): text is string => Boolean(text))

    expect(headers).toEqual([
      columnHeader('customerNumber'),
      columnHeader('name'),
      columnHeader('type'),
      columnHeader('contact'),
      columnHeader('language'),
      columnHeader('status'),
    ])
  })

  it('renders every persisted field as a toggleable column and the grid survives each toggle', async () => {
    route([customer({ id: 'c1', customerNumber: 'K-1001' })])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await screen.findByText('K-1001')
    await userEvent.click(screen.getByRole('button', { name: 'Columns' }))

    // The panel's checkboxes are the ones wrapped in a <label>; the grid's
    // own select-all / row checkboxes carry an aria-label instead.
    const panelBoxes = () =>
      screen.getAllByRole('checkbox').filter((box): box is HTMLInputElement => Boolean(box.closest('label')))

    const total = panelBoxes().length
    expect(total).toBeGreaterThanOrEqual(30) // 33 CustomerRead columns

    for (let i = 0; i < total; i += 1) {
      const box = panelBoxes()[i]
      if (box.disabled) continue
      fireEvent.click(box)
      expect(screen.getAllByText('K-1001').length).toBeGreaterThan(0)
    }

    // A formerly-hidden column is now on screen …
    expect(screen.getAllByRole('columnheader', { name: new RegExp(columnHeader('phoneMobile'), 'i') }).length).toBeGreaterThan(0)
    // … and toggling them all back leaves the grid intact.
    for (let i = 0; i < total; i += 1) {
      const box = panelBoxes()[i]
      if (box.disabled || !box.checked) continue
      fireEvent.click(box)
    }
    expect(screen.getAllByText('K-1001').length).toBeGreaterThan(0)
  }, 20000)

  it('the composite Contact cell shows mobile and email, stacks at comfortable, drops the secondary at compact', async () => {
    route([customer({ id: 'c1', phoneMobile: '+41 79 111 22 33', email: 'anna@bay.ch' })])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await screen.findByText('+41 79 111 22 33')
    expect(screen.getByText('anna@bay.ch')).toBeInTheDocument() // inline at default

    const cycleDensity = () => userEvent.click(screen.getByRole('button', { name: i18n.t('common.density.ariaLabel') }))

    await cycleDensity() // default -> comfortable
    expect(screen.getByText('+41 79 111 22 33')).toBeInTheDocument()
    expect(screen.getByText('anna@bay.ch')).toBeInTheDocument()

    await cycleDensity() // comfortable -> compact
    expect(screen.getByText('+41 79 111 22 33')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('anna@bay.ch')).not.toBeInTheDocument())
  })

  it('a customer with no mobile renders the Contact cell as just the email — no stray separator', async () => {
    route([customer({ id: 'c1', phoneMobile: null, email: 'solo@x.ch' })])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    const emailNode = await screen.findByText('solo@x.ch')
    const cell = emailNode.closest('[role="cell"]')
    expect(cell?.textContent).toBe('solo@x.ch')
    expect(screen.getAllByText('solo@x.ch')).toHaveLength(1)
  })
})

describe('CustomersListPage — filters track the column registry (ADR-058)', () => {
  it('offers only a condition the customer list endpoint can honour: select "is", and no "more than N days ago" on the date field', async () => {
    route()
    const user = userEvent.setup()
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await screen.findByRole('columnheader', { name: new RegExp(columnHeader('name'), 'i') })
    await user.click(screen.getByRole('button', { name: i18n.t('customersList.allCustomersView') }))
    await user.click(await screen.findByRole('button', { name: /new filter/i }))

    const combos = screen.getAllByRole('combobox')
    const fieldSelect = combos[0]
    const conditionSelect = combos[1]

    // Default field is a select field -> only "is", never "is not".
    expect(within(conditionSelect).getAllByRole('option').map((o) => o.textContent)).toEqual(['is'])

    // Switch to the date field -> every relative condition except
    // "more than N days ago" (there is no "changed before" parameter).
    await user.selectOptions(fieldSelect, columnHeader('changed'))
    const dateConditions = within(screen.getAllByRole('combobox')[1])
      .getAllByRole('option')
      .map((o) => o.textContent ?? '')
    expect(dateConditions.some((c) => /more than/i.test(c))).toBe(false)
    expect(dateConditions.length).toBeGreaterThan(0)
  })
})

describe('CustomersListPage — bulk Export (D-25)', () => {
  it('exports the selected rows in the currently visible columns, not a placeholder', async () => {
    exportSpy.mockClear()
    route([customer({ id: 'c1', customerNumber: 'K-1001' }), customer({ id: 'c2', customerNumber: 'K-1002' })])
    const user = userEvent.setup()
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    await screen.findByText('K-1001')
    // "Copy IDs" is gone.
    expect(screen.queryByText('Copy IDs')).not.toBeInTheDocument()

    await user.click(screen.getByLabelText('Select all'))
    await user.click(await screen.findByRole('button', { name: i18n.t('customersList.selection.export') }))

    expect(exportSpy).toHaveBeenCalledTimes(1)
    const [filename, columns, rows] = exportSpy.mock.calls[0] as [
      string,
      { header: string; value: (row: unknown) => string }[],
      { customerNumber: string }[],
    ]
    expect(filename).toMatch(/^customers_\d{4}-\d{2}-\d{2}\.csv$/)
    // Visible columns only, in on-screen order.
    expect(columns.map((c) => c.header)).toEqual([
      columnHeader('customerNumber'),
      columnHeader('name'),
      columnHeader('type'),
      columnHeader('contact'),
      columnHeader('language'),
      columnHeader('status'),
    ])
    // Both selected rows, and the accessor reaches real row data.
    expect(rows).toHaveLength(2)
    expect(columns[0].value(rows[0])).toBe('K-1001')
  })
})
