// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend } from '../../../test/fakeBackend'
import { SalesDocumentsSection } from './SalesDocumentsSection'

// KAN-217 PR-6 — the offer's documents open the kit's send dialog: the
// connected channels only, the preferred one pre-selected, the text as it
// will be sent, a refusal with its reason, the send, and the delivery chip.

const EMAIL = {
  channel: 'email', ready: true, notReadyReason: null, senderAddress: 'lea@garage-muster.ch',
  address: 'anna.muster@example.ch', allowed: true, refusal: null, language: 'de',
  text: 'Sehr geehrte Frau Muster\nIhre Offerte O-1', textEditable: true, delivery: 'attachment', blocked: null,
}
const SMS = {
  channel: 'sms', ready: true, notReadyReason: null, senderAddress: null, address: null, allowed: false,
  refusal: 'no_usable_contact', language: 'de', text: 'Ihre Offerte', textEditable: true, delivery: 'secure_link',
  blocked: 'recipient_refused',
}
const WHATSAPP = {
  channel: 'whatsapp', ready: false, notReadyReason: 'messaging_not_connected', senderAddress: null,
  address: '+41790000217', allowed: true, refusal: null, language: 'de', text: 'Hallo', textEditable: false,
  delivery: 'secure_link', blocked: 'messaging_not_connected',
}

function install({ sentStatus = 'sent' }: { sentStatus?: string } = {}) {
  let delivered: Record<string, unknown> | null = null
  const backend = installFakeBackend([
    {
      method: 'GET',
      match: /^\/sales\/offers\/o1\/documents$/,
      handler: () => ({
        items: [{ id: 'd1', ownerType: 'offer', ownerId: 'o1', version: 1, correspondenceLanguage: 'de', renderedAt: '2026-11-23T10:00:00Z', renderedBy: null, lastDelivery: delivered }],
      }),
    },
    { method: 'GET', match: /^\/sales\/documents\/d1\/pdf$/, handler: () => ({}) },
    {
      method: 'GET',
      match: /^\/sales\/documents\/d1\/send-options$/,
      handler: () => ({ documentId: 'd1', customerLabel: 'Anna Muster', preselectedChannel: 'email', channels: [EMAIL, SMS, WHATSAPP] }),
    },
    {
      method: 'POST',
      match: /^\/sales\/documents\/d1\/send$/,
      handler: () => {
        delivered = { deliveryId: 'x1', channel: 'email', status: sentStatus, sentAt: '2026-11-23T11:00:00Z', failureCode: null, providerStatus: null }
        return { __status: 201, body: { deliveryId: 'x1', channel: 'email', status: sentStatus, senderAddress: 'lea@garage-muster.ch', failureCode: null, providerStatus: null } }
      },
    },
  ])
  return backend
}

afterEach(() => cleanup())

async function openDialog() {
  const user = userEvent.setup()
  renderWithProviders(<SalesDocumentsSection ownerType="offer" ownerId="o1" />)
  await user.click(await screen.findByRole('button', { name: i18n.t('salesDocuments.send.action') }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('Anna Muster')
  return { user, dialog }
}

describe('DocumentSendDialog — the one send dialog on an offer (KAN-217 PR-6)', () => {
  it('offers the connected channels only and opens on the preferred one with its text', async () => {
    install()
    const { dialog } = await openDialog()
    expect(within(dialog).getByText(i18n.t('salesDocuments.send.channel.email'))).toBeInTheDocument()
    expect(within(dialog).getByText(i18n.t('salesDocuments.send.channel.sms'))).toBeInTheDocument()
    expect(within(dialog).queryByText(i18n.t('salesDocuments.send.channel.whatsapp'))).toBeNull()
    expect(within(dialog).getByRole('textbox', { name: i18n.t('salesDocuments.send.text') })).toHaveValue(EMAIL.text)
    expect(within(dialog).getByText(i18n.t('salesDocuments.send.to', { address: EMAIL.address }))).toBeInTheDocument()
    expect(within(dialog).getByText(i18n.t('salesDocuments.send.note.attachment', { sender: 'lea@garage-muster.ch' }))).toBeInTheDocument()
  })

  it("says Customers' refusal by its reason and does not send", async () => {
    const backend = install()
    const { user, dialog } = await openDialog()
    await user.click(within(dialog).getByText(i18n.t('salesDocuments.send.channel.sms')))
    expect(within(dialog).getByText(i18n.t('salesDocuments.send.refusal.no_usable_contact'))).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: i18n.t('salesDocuments.send.submit') })).toBeDisabled()
    expect(backend.callsTo(/\/send$/, 'POST')).toHaveLength(0)
  })

  it('sends no text when the author did not edit it, and the document then shows its delivery chip', async () => {
    const backend = install()
    const { user, dialog } = await openDialog()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('salesDocuments.send.submit') }))
    await waitFor(() => expect(backend.callsTo(/\/send$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/send$/, 'POST')
    expect(call.body).toEqual({ channel: 'email' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await screen.findByText(i18n.t('salesDocuments.send.success', { channel: i18n.t('salesDocuments.send.channel.email') }))).toBeInTheDocument()
    expect(await screen.findByText((text) => text.startsWith(`${i18n.t('salesDocuments.send.status.sent')} · ${i18n.t('salesDocuments.send.channel.email')}`))).toBeInTheDocument()
  })

  it("sends the author's own wording when they changed it", async () => {
    const backend = install()
    const { user, dialog } = await openDialog()
    const text = within(dialog).getByRole('textbox', { name: i18n.t('salesDocuments.send.text') })
    await user.clear(text)
    await user.type(text, 'Guten Tag Frau Muster')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('salesDocuments.send.submit') }))
    await waitFor(() => expect(backend.callsTo(/\/send$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/send$/, 'POST')[0].body).toEqual({ channel: 'email', text: 'Guten Tag Frau Muster' })
  })

  it('a send the provider refused says it was not delivered', async () => {
    install({ sentStatus: 'failed' })
    const { user, dialog } = await openDialog()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('salesDocuments.send.submit') }))
    expect(await screen.findByText(i18n.t('salesDocuments.send.failedResult'))).toBeInTheDocument()
  })
})
