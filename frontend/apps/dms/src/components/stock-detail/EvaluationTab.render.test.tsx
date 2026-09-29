// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { StockValuationRefRead } from '../../api/types'
import { EvaluationTab } from './EvaluationTab'

// KAN-101 — ADR-048 as amended: "a manual figure is marked manual and is
// never presented as a provider valuation". The tab used to print Stock's
// raw `source` string ("manual"), untranslated.

function renderTab(ref: StockValuationRefRead) {
  installFakeBackend([{ match: /^\/inventory\/stock-items\/st-1\/valuation$/, handler: () => ref }])
  return renderWithProviders(<EvaluationTab stockItemId="st-1" locale="de-CH" />)
}

const REF: StockValuationRefRead = {
  valuationId: 'v-1',
  amount: '12000.00',
  valuedAt: '2026-09-29T08:00:00Z',
  source: 'manual',
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('EvaluationTab — the source is marked, never printed raw (KAN-101)', () => {
  it('marks a manual figure with the translated Manual badge', async () => {
    renderTab(REF)

    expect(await screen.findByText(i18n.t('valuationSource.manual'))).toBeInTheDocument()
    expect(screen.queryByText('manual')).not.toBeInTheDocument()
  })

  it('translates the marker into the user language', async () => {
    await i18n.changeLanguage('fr')
    renderTab(REF)

    expect(await screen.findByText('Manuel')).toBeInTheDocument()
  })

  it('marks a provider figure as auto-i-dat', async () => {
    renderTab({ ...REF, source: 'auto_i_dat' })

    expect(await screen.findByText('auto-i-dat')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('valuationSource.manual'))).not.toBeInTheDocument()
  })

  it('shows the empty state when Stock holds no pointer', async () => {
    renderTab({ valuationId: null, amount: null, valuedAt: null, source: null })

    expect(await screen.findByText(i18n.t('stockDetail.evaluation.empty'))).toBeInTheDocument()
  })
})
