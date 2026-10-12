// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from './index'
import en from './locales/en.json'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { ContactPointsEditor } from '../components/customer-detail/ContactPointsEditor'
import { CustomersListPage } from '../pages/CustomersListPage'

// KAN-291 — two accessible names the ui-kit used to hardcode in English: the
// grid's selection header checkbox ("Select all") and a contact row's menu
// trigger ("<label> row actions"). Screen-reader users in DE/FR/IT heard
// English; both now come from the caller's translation.

// jsdom has no layout, so TanStack Virtual would window down to zero rows.
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

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ui-kit accessible names are translated (KAN-291)', () => {
  it('the grid\'s select-all checkbox is named in the UI language (fr)', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([{ method: 'GET', match: /^\/customers$/, handler: () => customerPage([customer({ id: 'c1' })]) }])
    renderWithProviders(<CustomersListPage />, { route: '/customers' })

    expect(await screen.findByRole('checkbox', { name: 'Tout sélectionner' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: en.common.selectAllLabel })).toBeNull()
  })

  it('a contact row\'s menu trigger is named in the UI language (fr)', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([])
    const label = i18n.t('customerDetail.contactPoints.phoneNumbers')
    renderWithProviders(
      <ContactPointsEditor<'mobile'>
        label={label}
        addLabel={i18n.t('customerDetail.contactPoints.addPhone')}
        typeOptions={[{ value: 'mobile', label: 'Mobile' }]}
        rows={[{ id: 'p1', type: 'mobile', value: '+41791112233', isPrimary: true, consentGranted: false }]}
        newRowType="mobile"
        renderValueEditor={(value) => <span>{value}</span>}
        onCreate={async () => {}}
        onUpdate={async () => {}}
        onDelete={async () => {}}
      />,
    )

    expect(screen.getByRole('button', { name: `Actions de la ligne ${label}` })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `${label} row actions` })).toBeNull()
  })
})
