// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { EquipmentRead } from '../../api/types'
import { PublishingTab } from './PublishingTab'

// KAN-152 item 3 — the equipment card reads the generated EquipmentRead
// (the endpoint now declares its response_model), never a hand-written
// interface. This pins that the card still renders what the API returns.

const EQUIPMENT: EquipmentRead = {
  ausstattungCodes: ['A1'],
  extras: ['Anhängerkupplung'],
  eigenschaften: ['Nichtraucher'],
  providerAusstattung: { de: 'Navigationssystem' },
}

describe('PublishingTab — equipment card', () => {
  it('renders the three equipment lists the API returns', async () => {
    installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => [] },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)

    expect(await screen.findByText(i18n.t('stockDetail.publishing.equipment.title'))).toBeInTheDocument()
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.getByText('Anhängerkupplung')).toBeInTheDocument()
    expect(screen.getByText('Nichtraucher')).toBeInTheDocument()
  })
})
