// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// Configurator C-A (KAN-39), exit criterion 3: the three new canonical
// lists must be authorable in the FR-V-11 admin screen. That screen drives
// its list picker off `REFERENCE_LIST_CODES`; this proves a
// platform_admin can actually select each one — not just that the constant
// contains the string.

const emptyValuesRoute: FakeRoute = {
  method: 'GET',
  match: /\/reference-data\//,
  handler: () => ({ items: [], nextCursor: null }),
}

describe('ReferenceDataPage — the configurator C-A canonical lists', () => {
  it('offers engine_cycle, valuation_classification and option_relation_type in the list picker', async () => {
    const user = userEvent.setup()
    installFakeBackend([emptyValuesRoute])

    const { container } = renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    // The list picker is a Mantine Select carrying this aria-label.
    const picker = await screen.findByText('Referenzliste fuel_type')
    expect(picker).toBeInTheDocument()
    const pickerInput = container.querySelector<HTMLInputElement>(
      `input[aria-label="${i18n.t('referenceData.list.pickLabel')}"]`,
    )
    expect(pickerInput).not.toBeNull()
    await user.click(pickerInput!)

    const listbox = await screen.findByRole('listbox')
    for (const code of ['engine_cycle', 'valuation_classification', 'option_relation_type']) {
      expect(within(listbox).getByRole('option', { name: code })).toBeInTheDocument()
    }
  })
})
