// @vitest-environment jsdom
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend, status } from '../../test/fakeBackend'
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

describe('PublishingTab — one Idempotency-Key per channel write (KAN-266)', () => {
  it('publish, unpublish and publish again are three submissions, each under its own key', async () => {
    // A successful publish or unpublish retires the channel's key, so a later
    // identical request is a new submission, never a replay that writes nothing.
    const user = userEvent.setup()
    let state: 'not_published' | 'published' = 'not_published'
    const publishing = () => ({
      id: 'pub-1', stockItemId: 'st-1', channel: 'autoscout24', state, transmissionStatus: 'not_sent',
      lastPublishedAt: null, lastAttemptedAt: null, lastTransmissionError: null, zusatztitel: null,
      bemerkungen: null, zustandsbeschreibung: null, haendlerbemerkungen: null, youtubeUrl: null,
      pdfDocumentRef: null, version: 1, blockingConditions: [],
    })
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => [] },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/publishing\/autoscout24$/, handler: publishing },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/st-1\/publishing\/autoscout24\/publish$/,
        handler: () => {
          state = 'published'
          return publishing()
        },
      },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/st-1\/publishing\/autoscout24\/unpublish$/,
        handler: () => {
          state = 'not_published'
          return publishing()
        },
      },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const keys = () =>
      backend.calls
        .filter((call) => call.method === 'POST' && /\/publishing\/autoscout24\/(un)?publish$/.test(call.pathname))
        .map((call) => call.headers.get('Idempotency-Key'))
    const publishButton = () =>
      screen.findByRole('button', { name: i18n.t('stockDetail.publishing.publishTo', { channel: 'AutoScout24' }) })

    await user.click(await publishButton())
    await waitFor(() => expect(keys()).toHaveLength(1))
    await user.click(
      await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.unpublish', { channel: 'AutoScout24' }) }),
    )
    await user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.unpublishConfirmSubmit') }))
    await waitFor(() => expect(keys()).toHaveLength(2))
    await user.click(await publishButton())
    await waitFor(() => expect(keys()).toHaveLength(3))

    const [first, unpublish, again] = keys()
    expect(first).toBeTruthy()
    expect(unpublish).toBeTruthy()
    expect(again).toBeTruthy()
    expect(new Set([first, unpublish, again]).size).toBe(3)
  })

  it('a second identical publish after a successful one is a new submission, not a replay', async () => {
    // The listing still reads unpublished (say the transmission was refused):
    // the same publish again must reach the server, so it needs a new key.
    const user = userEvent.setup()
    const publishing = () => ({
      id: 'pub-1', stockItemId: 'st-1', channel: 'autoscout24', state: 'not_published', transmissionStatus: 'failed',
      lastPublishedAt: null, lastAttemptedAt: null, lastTransmissionError: null, zusatztitel: null,
      bemerkungen: null, zustandsbeschreibung: null, haendlerbemerkungen: null, youtubeUrl: null,
      pdfDocumentRef: null, version: 1, blockingConditions: [],
    })
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => [] },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/publishing\/autoscout24$/, handler: publishing },
      { method: 'POST', match: /\/publishing\/autoscout24\/publish$/, handler: publishing },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const keys = () => backend.callsTo(/\/publish$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    const publish = async () =>
      user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.publishTo', { channel: 'AutoScout24' }) }))

    await publish()
    await waitFor(() => expect(keys()).toHaveLength(1))
    await publish()
    await waitFor(() => expect(keys()).toHaveLength(2))

    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})

describe('PublishingTab — one Idempotency-Key per photo reorder (KAN-266)', () => {
  it('the same reorder sent again after a successful one is a new submission', async () => {
    // The server answers with the order it keeps (here: unchanged), so the
    // same move is sent twice; the second must not replay the first answer.
    const user = userEvent.setup()
    const media = [
      { id: 'm-1', url: 'https://cdn.example.ch/1.jpg', position: 1 },
      { id: 'm-2', url: 'https://cdn.example.ch/2.jpg', position: 2 },
    ]
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => media },
      { method: 'POST', match: /^\/inventory\/stock-items\/st-1\/media\/reorder$/, handler: () => media },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const keys = () => backend.callsTo(/\/media\/reorder$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    const moveFirstRight = async () =>
      user.click((await screen.findAllByRole('button', { name: i18n.t('stockDetail.publishing.media.moveRight') }))[0])

    await moveFirstRight()
    await waitFor(() => expect(keys()).toHaveLength(1))
    await moveFirstRight()
    await waitFor(() => expect(keys()).toHaveLength(2))

    const calls = backend.callsTo(/\/media\/reorder$/, 'POST')
    expect(calls[1].body).toEqual(calls[0].body)
    expect(keys()[0]).toBeTruthy()
    expect(keys()[1]).toBeTruthy()
    expect(keys()[1]).not.toBe(keys()[0])
  })
})

describe('PublishingTab — a failed save says so and keeps its key (KAN-272)', () => {
  const refusal = (message: string) => status(503, { error: { code: 'unavailable', message, details: null } })
  // A dropped connection: no API message, so the action's translated fallback.
  const dropped = () => Promise.reject(new TypeError('Failed to fetch'))
  const publishingRow = (state: 'not_published' | 'published') => ({
    id: 'pub-1', stockItemId: 'st-1', channel: 'autoscout24', state, transmissionStatus: 'transmitted',
    lastPublishedAt: null, lastAttemptedAt: null, lastTransmissionError: null, zusatztitel: null,
    bemerkungen: null, zustandsbeschreibung: null, haendlerbemerkungen: null, youtubeUrl: null,
    pdfDocumentRef: null, version: 1, blockingConditions: [],
  })
  const media = [
    { id: 'm-1', url: 'https://cdn.example.ch/1.jpg', position: 1 },
    { id: 'm-2', url: 'https://cdn.example.ch/2.jpg', position: 2 },
  ]
  /** The first request fails with `failure()`; every later one gets `ok()`. */
  const failOnce = (failure: () => unknown, ok: () => unknown) => {
    let failed = false
    return () => {
      if (failed) return ok()
      failed = true
      return failure()
    }
  }
  const keys = (backend: ReturnType<typeof installFakeBackend>, pattern: RegExp) =>
    backend.callsTo(pattern, 'POST').map((call) => call.headers.get('Idempotency-Key'))

  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('a refused publish shows the server message on the card, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    let state: 'not_published' | 'published' = 'not_published'
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => [] },
      { method: 'GET', match: /\/publishing\/autoscout24$/, handler: () => publishingRow(state) },
      {
        method: 'POST',
        match: /\/publishing\/autoscout24\/publish$/,
        handler: failOnce(() => refusal('Marketplace unavailable'), () => {
          state = 'published'
          return publishingRow(state)
        }),
      },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const publish = async () =>
      user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.publishTo', { channel: 'AutoScout24' }) }))

    await publish()
    expect((await screen.findByRole('alert')).textContent).toBe('Marketplace unavailable')

    await publish()
    await waitFor(() => expect(keys(backend, /\/publish$/)).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    const [failedKey, retryKey] = keys(backend, /\/publish$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a failed unpublish keeps the confirmation open with a translated message, and the retry reuses the key', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    let state: 'not_published' | 'published' = 'published'
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => [] },
      { method: 'GET', match: /\/publishing\/autoscout24$/, handler: () => publishingRow(state) },
      {
        method: 'POST',
        match: /\/publishing\/autoscout24\/unpublish$/,
        handler: failOnce(dropped, () => {
          state = 'not_published'
          return publishingRow(state)
        }),
      },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="fr-CH" />)

    await user.click(
      await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.unpublish', { channel: 'AutoScout24' }) }),
    )
    const confirm = await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.unpublishConfirmSubmit') })
    await user.click(confirm)

    const dialog = await screen.findByRole('dialog')
    expect((await within(dialog).findByRole('alert')).textContent).toBe(i18n.t('stockDetail.publishing.unpublishError'))

    await user.click(within(dialog).getByRole('button', { name: i18n.t('stockDetail.publishing.unpublishConfirmSubmit') }))
    await waitFor(() => expect(keys(backend, /\/unpublish$/)).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const [failedKey, retryKey] = keys(backend, /\/unpublish$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a failed photo add says so, offers the same URL again, and the retry reuses the key', async () => {
    const user = userEvent.setup()
    const rows: { id: string; url: string; position: number }[] = []
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => rows },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/st-1\/media$/,
        handler: failOnce(() => refusal('Photo store unavailable'), () => {
          const row = { id: 'm-1', url: 'https://cdn.example.ch/1.jpg', position: 1 }
          rows.push(row)
          return status(201, row)
        }),
      },
    ])
    // The user accepts whatever the prompt offers; the first time, they type the URL.
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockImplementation((_message?: string, offered?: string) => offered ?? 'https://cdn.example.ch/1.jpg')
    onTestFinished(() => prompt.mockRestore())
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const addPhoto = async () =>
      user.click(await screen.findByRole('button', { name: i18n.t('stockDetail.publishing.media.addPhoto') }))

    await addPhoto()
    expect((await screen.findByRole('alert')).textContent).toBe('Photo store unavailable')

    await addPhoto()
    await waitFor(() => expect(keys(backend, /\/media$/)).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    expect(prompt.mock.calls[1][1]).toBe('https://cdn.example.ch/1.jpg')
    const [failedKey, retryKey] = keys(backend, /\/media$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })

  it('a failed photo removal shows a translated message', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => media },
      { method: 'DELETE', match: /^\/inventory\/stock-items\/st-1\/media\/m-1$/, handler: dropped },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="it-CH" />)

    await user.click((await screen.findAllByRole('button', { name: i18n.t('stockDetail.publishing.media.remove') }))[0])
    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('stockDetail.publishing.media.removeError'))
  })

  it('a failed reorder says so, and the same move sent again reuses the key', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { match: /^\/inventory\/stock-items\/st-1\/equipment$/, handler: () => EQUIPMENT },
      { method: 'GET', match: /^\/inventory\/stock-items\/st-1\/media$/, handler: () => media },
      {
        method: 'POST',
        match: /^\/inventory\/stock-items\/st-1\/media\/reorder$/,
        handler: failOnce(dropped, () => media),
      },
    ])
    renderWithProviders(<PublishingTab stockItemId="st-1" locale="de-CH" />)
    const moveFirstRight = async () =>
      user.click((await screen.findAllByRole('button', { name: i18n.t('stockDetail.publishing.media.moveRight') }))[0])

    await moveFirstRight()
    expect((await screen.findByRole('alert')).textContent).toBe(i18n.t('stockDetail.publishing.media.reorderError'))

    await moveFirstRight()
    await waitFor(() => expect(keys(backend, /\/media\/reorder$/)).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())

    const [failedKey, retryKey] = keys(backend, /\/media\/reorder$/)
    expect(failedKey).toBeTruthy()
    expect(retryKey).toBe(failedKey)
  })
})
