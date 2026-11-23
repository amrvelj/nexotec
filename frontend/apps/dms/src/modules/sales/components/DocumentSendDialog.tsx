import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Loader, Modal } from '@mantine/core'
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

/** Why a channel cannot send now, in the UI language: Customers' refusal by
 * its own reason, every other block by its code. */
function blockedMessage(t: TFunction, option: SalesSendChannelOption): string | null {
  if (option.blocked == null) return null
  if (option.blocked === 'recipient_refused') {
    const key = option.refusal ? `salesDocuments.send.refusal.${option.refusal}` : null
    return key && t(key, { defaultValue: '' }) ? t(key) : t('salesDocuments.send.refusal.generic')
  }
  const key = `salesDocuments.send.blocked.${option.blocked}`
  return t(key, { defaultValue: '' }) ? t(key) : t('salesDocuments.send.blocked.generic')
}

function toChannel(t: TFunction, option: SalesSendChannelOption): SendMessageChannel {
  const note =
    option.delivery === 'attachment'
      ? option.senderAddress
        ? t('salesDocuments.send.note.attachment', { sender: option.senderAddress })
        : null
      : t('salesDocuments.send.note.secureLink')
  return {
    channel: option.channel,
    label: t(`salesDocuments.send.channel.${option.channel}`),
    recipientLine: option.address ? t('salesDocuments.send.to', { address: option.address }) : null,
    note,
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

  if (opened && (optionsQuery.isLoading || optionsQuery.isError)) {
    return (
      <Modal opened onClose={close} title={t('salesDocuments.send.title')}>
        {optionsQuery.isLoading ? (
          <Loader size="sm" />
        ) : (
          <Alert color="red">{t('salesDocuments.send.errors.loadOptions')}</Alert>
        )}
      </Modal>
    )
  }

  const options = optionsQuery.data
  const channels = (options?.channels ?? []).filter((option) => option.ready).map((option) => toChannel(t, option))

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
      opened={opened && options != null}
      onClose={close}
      recipientName={options?.customerLabel ?? ''}
      channels={channels}
      preselectedChannel={options?.preselectedChannel ?? null}
      onSend={send}
      sending={sending}
      error={error}
      labels={{
        title: t('salesDocuments.send.title'),
        channels: t('salesDocuments.send.channels'),
        text: t('salesDocuments.send.text'),
        textFixed: t('salesDocuments.send.textFixed'),
        noChannel: t('salesDocuments.send.noChannel'),
        chooseChannel: t('salesDocuments.send.chooseChannel'),
        send: t('salesDocuments.send.submit'),
        cancel: t('salesDocuments.send.cancel'),
      }}
    />
  )
}
