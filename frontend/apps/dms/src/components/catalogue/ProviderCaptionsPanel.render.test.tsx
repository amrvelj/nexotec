// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
import { ProviderCaptionsPanel } from './ProviderCaptionsPanel'

// KAN-588 — platform staff see per provider and language when the provider's
// code vocabulary was last fetched, and refresh it by hand.

const BEFORE = {
  items: [
    { provider: 'auto_i_dat', providerName: 'auto-i-dat (identAPI)', language: 'de', fetchedAt: '2026-11-07T09:00:00Z', captionCount: 412 },
    { provider: 'auto_i_dat', providerName: 'auto-i-dat (identAPI)', language: 'fr', fetchedAt: '2026-11-07T09:00:02Z', captionCount: 412 },
  ],
}
const AFTER = {
  items: ['de', 'fr', 'it', 'en'].map((language) => ({
    provider: 'auto_i_dat', providerName: 'auto-i-dat (identAPI)', language, fetchedAt: '2026-11-13T10:15:00Z', captionCount: 415,
  })),
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('ProviderCaptionsPanel (KAN-588)', () => {
  it('shows each language’s last fetch and marks a language never fetched', async () => {
    installFakeBackend([{ method: 'GET', match: /^\/integrations\/captions\/fetches$/, handler: () => BEFORE }])
    renderWithProviders(<ProviderCaptionsPanel />)

    expect(await screen.findAllByText(/412 Codes/)).toHaveLength(2)
    const table = screen.getByRole('table', { name: i18n.t('providerCaptions.title') })
    const row = within(table).getAllByRole('row')[1]
    expect(within(row).getByText('auto-i-dat (identAPI)')).toBeInTheDocument()
    expect(within(row).getAllByText(i18n.t('providerCaptions.notFetched'))).toHaveLength(2)
  })

  it('refreshes the vocabulary and shows the new fetch times', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/integrations\/captions\/fetches$/, handler: () => BEFORE },
      { method: 'POST', match: /^\/integrations\/captions\/refresh$/, handler: () => AFTER },
    ])
    const user = userEvent.setup()
    renderWithProviders(<ProviderCaptionsPanel />)

    await user.click(await screen.findByRole('button', { name: i18n.t('providerCaptions.refresh') }))

    expect(await screen.findByText(i18n.t('providerCaptions.refreshed'))).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/415 Codes/)).toHaveLength(4))
    expect(screen.queryByText(i18n.t('providerCaptions.notFetched'))).toBeNull()
    expect(backend.calls.some((c) => c.method === 'POST' && c.headers.get('Idempotency-Key'))).toBe(true)
  })

  it('says which languages landed when the provider fails part-way, in the user’s language', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { method: 'GET', match: /^\/integrations\/captions\/fetches$/, handler: () => BEFORE },
      {
        method: 'POST',
        match: /^\/integrations\/captions\/refresh$/,
        handler: () =>
          status(422, {
            error: {
              code: 'unprocessable_entity',
              message: 'The provider could not be asked for its captions.',
              details: { reason: 'provider_unavailable', languagesFetched: ['de', 'fr'] },
            },
          }),
      },
    ])
    const user = userEvent.setup()
    renderWithProviders(<ProviderCaptionsPanel />)

    await user.click(await screen.findByRole('button', { name: i18n.t('providerCaptions.refresh') }))

    expect(await screen.findByText(i18n.t('providerCaptions.partial', { languages: 'DE, FR' }))).toBeInTheDocument()
    expect(screen.queryByText('The provider could not be asked for its captions.')).toBeNull()
  })

  it('a contract refusal keeps its own words even when some languages landed', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/integrations\/captions\/fetches$/, handler: () => BEFORE },
      {
        method: 'POST',
        match: /^\/integrations\/captions\/refresh$/,
        handler: () =>
          status(409, {
            error: {
              code: 'conflict',
              message: "Captions are not in this Betrieb's provider contract.",
              details: { reason: 'not_entitled', languagesFetched: ['de'] },
            },
          }),
      },
    ])
    const user = userEvent.setup()
    renderWithProviders(<ProviderCaptionsPanel />)

    await user.click(await screen.findByRole('button', { name: i18n.t('providerCaptions.refresh') }))

    expect(await screen.findByText(i18n.t('apiRefusal.reasons.not_entitled'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('providerCaptions.partial', { languages: 'DE' }))).toBeNull()
  })
})
