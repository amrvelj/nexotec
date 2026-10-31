// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, type FakeRoute } from '../../test/fakeBackend'
import { ConfigurationResyncPanel } from './ConfigurationResyncPanel'

// KAN-190 item 2 — the re-sync panel showed a coded field's raw canonical
// code (`petrol`, `diesel`); reference data is rendered by its label in the
// UI language, as the configuration's summary card renders it.

const list = (listCode: string, values: [string, string, string][]): FakeRoute => ({
  method: 'GET',
  match: new RegExp(`/reference-data/${listCode}$`),
  handler: () => ({
    items: values.map(([valueCode, labelDe, labelFr], i) => ({
      id: `rv-${valueCode}`, listCode, valueCode, labelDe, labelFr, labelIt: labelDe, labelEn: labelDe,
      sortOrder: i, active: true, version: 1,
    })),
    nextCursor: null,
  }),
})

function install() {
  installFakeBackend([
    {
      method: 'GET',
      match: /^\/configurations\/cfg-1\/resync$/,
      handler: () => [
        { field: 'fuel_type', current: 'petrol', catalogue: 'diesel', overridden: false },
        { field: 'engine_cycle', current: null, catalogue: 'four_stroke', overridden: false },
        { field: 'ps', current: 150, catalogue: 245, overridden: true },
      ],
    },
    list('fuel_type', [['petrol', 'Benzin', 'Essence'], ['diesel', 'Diesel', 'Gazole']]),
    list('engine_cycle', [['four_stroke', '4-Takt', 'Quatre temps']]),
  ])
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('ConfigurationResyncPanel — values in the UI language (KAN-190)', () => {
  it('shows a coded field and a spec reference field by their labels, a number as it is', async () => {
    await i18n.changeLanguage('fr')
    install()
    renderWithProviders(<ConfigurationResyncPanel configurationId="cfg-1" version={3} onClose={() => {}} onApplied={() => {}} />)

    const table = await screen.findByTestId('resync-fields')
    const row = (label: string) => within(table).getByText(label).closest('tr') as HTMLElement

    const fuel = row(i18n.t('catalogueBrowse.columns.fuelType'))
    expect(await within(fuel).findByText('Essence')).toBeInTheDocument()
    expect(within(fuel).getByText('Gazole')).toBeInTheDocument()
    expect(within(table).queryByText('petrol')).not.toBeInTheDocument()
    expect(within(table).queryByText('diesel')).not.toBeInTheDocument()

    const cycle = row(i18n.t('catalogueBrowse.columns.engineCycle'))
    expect(await within(cycle).findByText('Quatre temps')).toBeInTheDocument()
    expect(within(cycle).getByText('—')).toBeInTheDocument()

    const ps = row(i18n.t('catalogueBrowse.columns.ps'))
    expect(within(ps).getByText('150')).toBeInTheDocument()
    expect(within(ps).getByText('245')).toBeInTheDocument()
  })
})
