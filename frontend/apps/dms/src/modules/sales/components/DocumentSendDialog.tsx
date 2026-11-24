import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { SendMessageDialog, type SendMessageChannel } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { apiRefusalText } from '../../../utils/apiRefusal'
import type { SalesMessageDeliveryRead, SalesSendChannelOption, SalesSendOptions } from '../../../api/types'

export interface DocumentSendDialogProps {
  documentId: string
  opened: boolean
  onClose: () => void
  /** Called with the delivery record once the server answered the send (a
   * `failed` record included: the provider's answer is the record's). */
  onSent: (delivery: SalesMessageDeliveryRead) => void
}

/** A mailbox that is connected but cannot send: the channel is offered with
 * its reason, so the author learns to reconnect it. A channel with nothing
 * connected is not offered at all (FR-A-52, «connected channels only»). */
const FAILING_CONNECTION = new Set(['own_mailbox_in_error', 'business_mailbox_in_error'])

/** Why a channel cannot send now, in the UI language: Customers' refusal by
 * its own reason, every other block by its code. A missing standard text is
 * no block where the author may write their own: it is said as a note. */
function blockedMessage(t: TFunction, option: SalesSendChannelOption): string | null {
  if (option.blocked == null) return null
  if (option.blocked === 'message_text_missing' && option.textEditable) return null
  // WhatsApp's text is fixed: the author cannot write one, only create and approve it.
  if (option.blocked === 'message_text_missing') return t('salesDocuments.send.blocked.message_text_missing_fixed')
  if (option.blocked === 'recipient_refused') {
    const key = option.refusal ? `salesDocuments.send.refusal.${option.refusal}` : null
    return key && t(key, { defaultValue: '' }) ? t(key) : t('salesDocuments.send.refusal.generic')
  }
  const key = `salesDocuments.send.blocked.${option.blocked}`
  return t(key, { defaultValue: '' }) ? t(key) : t('salesDocuments.send.blocked.generic')
}

function toChannel(t: TFunction, option: SalesSendChannelOption): SendMessageChannel {
  const delivery =
    option.delivery === 'attachment'
      ? option.senderAddress
        ? t('salesDocuments.send.note.attachment', { sender: option.senderAddress })
        : null
      : t('salesDocuments.send.note.secureLink')
  const textMissing = option.blocked === 'message_text_missing' && option.textEditable
  const note = [textMissing ? t('salesDocuments.send.blocked.message_text_missing') : null, delivery]
    .filter((part): part is string => part != null)
    .join(' ')
  return {
    channel: option.channel,
    label: t(`salesDocuments.send.channel.${option.channel}`),
    recipientLine: option.address ? t('salesDocuments.send.to', { address: option.address }) : null,
    note: note || null,
    text: option.text,
    textEditable: option.textEditable,
    blockedMessage: blockedMessage(t, option),
  }
}

/**
 * KAN-217 PR-6 — Sales opens the kit's one send dialog for a document: it
 * reads the options (`GET …/send-options`) and runs its own send route. Only
 * the channels the Betrieb can send on are offered (FR-A-52).
 */
export function DocumentSendDialog({ documentId, opened, onClose, onSent }: DocumentSendDialogProps) {
  const { t } = useTranslation()
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sendKey = useIdempotencyKey()

  const optionsQuery = useQuery({
    queryKey: ['sales-document-send-options', documentId],
    queryFn: () => api.get<SalesSendOptions>(`/sales/documents/${documentId}/send-options`),
    enabled: opened,
    staleTime: 0,
  })

  const close = () => {
    setError(null)
    onClose()
  }

  const options = optionsQuery.data
  const channels = (options?.channels ?? [])
    .filter((option) => option.ready || FAILING_CONNECTION.has(option.notReadyReason ?? ''))
    .map((option) => toChannel(t, option))

  const send = async (channel: string, text: string | undefined) => {
    setSending(true)
    setError(null)
    const path = `/sales/documents/${documentId}/send`
    const body = text === undefined ? { channel } : { channel, text }
    try {
      const delivery = await api.post<SalesMessageDeliveryRead>(path, body, sendKey.headers([path, body]))
      sendKey.renew()
      onSent(delivery)
    } catch (err) {
      setError(apiRefusalText(t, err, 'salesDocuments.send.errors.generic'))
    } finally {
      setSending(false)
    }
  }

  return (
    <SendMessageDialog
      opened={opened}
      onClose={close}
      recipientName={options?.customerLabel ?? ''}
      channels={channels}
      preselectedChannel={options?.preselectedChannel ?? null}
      onSend={send}
      sending={sending}
      error={error}
      loading={optionsQuery.isLoading}
      loadError={optionsQuery.isError ? t('salesDocuments.send.errors.loadOptions') : null}
      labels={{
        title: t('salesDocuments.send.title'),
        channels: t('salesDocuments.send.channels'),
        text: t('salesDocuments.send.text'),
        textFixed: t('salesDocuments.send.textFixed'),
        noChannel: t('salesDocuments.send.noChannel'),
        chooseChannel: t('salesDocuments.send.chooseChannel'),
        send: t('salesDocuments.send.submit'),
        cancel: t('salesDocuments.send.cancel'),
        close: t('common.overlay.close'),
      }}
    />
  )
}
