// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend } from '../../../test/fakeBackend'
import { ServiceOrdersListPage } from './ServiceOrdersListPage'
import { ServiceOrderDetailPage } from './ServiceOrderDetailPage'
import { HISTORY, order, summary } from '../test/serviceOrderFixtures'

// KAN-810: every string of the workshop order screens in four languages (no hardcoded text).
// The expected texts are written out, not derived through i18n.t(), so a key falling back to
// another language would fail here.

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

const EXPECTED = {
  fr: { title: "Ordres d'atelier", state: 'Accepté', newOrder: 'Nouvel ordre', tab: 'Aperçu', totals: "Total de l'ordre par payeur", billing: 'Client', addJob: 'Ajouter un groupe', rounding: 'Arrondi' },
  it: { title: "Ordini d'officina", state: 'Accettato', newOrder: 'Nuovo ordine', tab: 'Panoramica', totals: "Totale dell'ordine per pagatore", billing: 'Cliente', addJob: 'Aggiungi gruppo', rounding: 'Arrotondamento' },
  en: { title: 'Workshop orders', state: 'Accepted', newOrder: 'New order', tab: 'Overview', totals: 'Order total per payer', billing: 'Customer', addJob: 'Add group', rounding: 'Rounding' },
} as const

function install() {
  installFakeBackend([
    { method: 'GET', match: /^\/service-orders$/, handler: () => ({ items: [summary()], nextCursor: null, total: 1, totalIsEstimate: false }) },
    { method: 'GET', match: /^\/service-orders\/order-1$/, handler: () => order() },
    { method: 'GET', match: /\/history$/, handler: () => HISTORY },
  ])
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe.each(Object.entries(EXPECTED))('the workshop order screens in %s', (language, expected) => {
  it('the list', async () => {
    await i18n.changeLanguage(language)
    install()
    renderWithProviders(<ServiceOrdersListPage />, { route: '/aftersales' })
    expect(await screen.findByRole('heading', { level: 2, name: expected.title })).toBeInTheDocument()
    expect(await screen.findByText(expected.state)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: expected.newOrder })).toBeInTheDocument()
  })

  it('the order page', async () => {
    await i18n.changeLanguage(language)
    install()
    renderWithProviders(
      <Routes>
        <Route path="/aftersales/orders/:id" element={<ServiceOrderDetailPage />} />
      </Routes>,
      { route: '/aftersales/orders/order-1' },
    )
    expect(await screen.findByRole('tab', { name: expected.tab })).toBeInTheDocument()
    expect(screen.getByText(expected.totals)).toBeInTheDocument()
    expect(screen.getByText(expected.billing)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: expected.addJob })).toBeInTheDocument()
    expect(screen.getAllByText(expected.rounding).length).toBeGreaterThan(0)
  })
})
