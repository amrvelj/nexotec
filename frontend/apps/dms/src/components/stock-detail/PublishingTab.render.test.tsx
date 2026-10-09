// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

describe('PublishingTab — one Idempotency-Key per photo added (KAN-266)', () => {
  it('the same photo added twice in a row is two submissions, each under its own key', async () => {
    // A successful add retires its key: the second, identical request is a
    // new photo, never a replay of the first answer that adds nothing.
    const user = userEvent.setup()
    const media: { id: string; url: string; position: number }[] = []
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => media },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/st-1\/media$/,
        handler: (req) => {
          const row = { id: `m-${media.length + 1}`, url: (req.body as { url: string }).url, position: media.length + 1 }
          media.push(row)
          return { __status: 201, body: row }
        },
      },
    ])
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('https://cdn.example.ch/1.jpg')
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const keys = () => backend.callsTo(/\/media$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

    await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.media.addPhoto') }))
    await waitFor(() => expect(keys()).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: i18n.t('stockDetail.publishing.media.addPhoto') }))
    await waitFor(() => expect(keys()).toHaveLength(2))
    prompt.mockRestore()

    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})
