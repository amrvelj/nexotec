import { useEffect, useState } from 'react'
import { Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type { ServiceOrderCancel, ServiceOrderRead } from '../../../api/types'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { RefusalAlert } from './order-detail/RefusalAlert'

interface CancelServiceOrderDialogProps {
  opened: boolean
  /** The order as the screen shows it: its id and the version the cancel is sent under (If-Match). */
  order: { id: string; version: number } | null
  onClose: () => void
  onCancelled: (order: ServiceOrderRead) => void
  /** A stale If-Match 409 offers «Neu laden», which reads the order again. */
  onReload: () => void
}

/**
 * D16 «Auftrag stornieren»: a required reason, then the order shows
 * «Storniert» with who, when and why (check 10). The rules are the server's
 * (table 3.1: a clock that has run refuses it, check 9); a refusal is said in
 * one sentence in the UI language. An empty reason holds the button back —
 * a mirror of the server's own check (`service_order_cancel_reason_required`).
 */
export function CancelServiceOrderDialog({ opened, order, onClose, onCancelled, onReload }: CancelServiceOrderDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setReason('')
      setError(null)
      idempotency.renew()
    }
  }, [opened, idempotency])

  const submit = async () => {
    if (!order) return
    setSubmitting(true)
    setError(null)
    try {
      const path = `/service-orders/${order.id}/cancel`
      const body: ServiceOrderCancel = { reason: reason.trim() }
      const updated = await api.post<ServiceOrderRead>(path, body, {
        'If-Match': String(order.version),
        ...idempotency.headers([path, body]),
      })
      idempotency.renew()
      onCancelled(updated)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('serviceOrder.cancel.title')}
      onSubmit={submit}
      submitLabel={t('serviceOrder.cancel.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={reason.trim() === ''}
      destructive
    >
      <Textarea
        label={t('serviceOrder.cancel.reason')}
        value={reason}
        onChange={(e) => setReason(e.currentTarget.value)}
        maxLength={500}
        autosize
        minRows={2}
        required
        data-autofocus
      />
      {error !== null && (
        <RefusalAlert
          error={error}
          fallbackKey="serviceOrder.cancel.error"
          onReload={() => {
            onReload()
            onClose()
          }}
        />
      )}
    </FormDialog>
  )
}
