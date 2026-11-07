import { useEffect, useId, useState } from 'react'
import { Checkbox, Stack, Text, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { useFieldHint } from '../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import type { StockItemRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'

interface PurchaseStornoDialogProps {
  opened: boolean
  onClose: () => void
  item: Pick<StockItemRead, 'id' | 'version' | 'stockNumber' | 'pipelineRef'>
  onStornoed: (updated: StockItemRead) => void
}

/**
 * KAN-146 (PRD-Stock FR-I-05; Anto, 2026-11-05) — the only place the SPA
 * calls `POST /v1/inventory/stock-items/{id}/purchase/storno`. The purchasing
 * admin reverses a booked purchase with a stated reason (the API refuses a
 * blank one; checked here first so the user is told before the round trip).
 * The car returns to pipeline with its VIN; a reservation stays; Sales then
 * derives «nicht fakturierbar» for its contract until it is bought again. A
 * refusal (left stock, invoice requested, a stale version) is said in the UI
 * language.
 *
 * KAN-572 (Anto, 2026-11-07, option C): on a trade-in's car — a pipeline item
 * a contract's confirmation created for its trade-in (`pipelineRef`
 * `contract:<id>:trade_in`) — the user says whether the car went back to the
 * customer who traded it in (`returnedToCustomer`). Ticked, the customer is
 * their owner and keeper again; unticked (the default), their history stays
 * as the purchase wrote it. Any other car is never asked (the API refuses it,
 * 422 `storno_return_not_trade_in`).
 */
export function isTradeInStockItem(item: Pick<StockItemRead, 'pipelineRef'>): boolean {
  return /^contract:[^:]+:trade_in$/.test(item.pipelineRef ?? '')
}

export function PurchaseStornoDialog({ opened, onClose, item, onStornoed }: PurchaseStornoDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [returnedToCustomer, setReturnedToCustomer] = useState(false)
  const tradeIn = isTradeInStockItem(item)
  const hint = useFieldHint()
  const returnedHintId = useId()
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // KAN-266: a retried submit replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setReason('')
      setReasonError(null)
      setReturnedToCustomer(false)
      setSubmitError(null)
      idempotency.renew()
    }
  }, [opened, idempotency])

  const submit = async () => {
    if (reason.trim().length === 0) {
      setReasonError(t('stockDetail.purchase.storno.reasonRequired'))
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const path = `/inventory/stock-items/${item.id}/purchase/storno`
      const updated = await api.post<StockItemRead>(
        path,
        tradeIn ? { reason: reason.trim(), returnedToCustomer } : { reason: reason.trim() },
        { 'If-Match': String(item.version), ...idempotency.headers([path]) },
      )
      idempotency.renew()
      onStornoed(updated)
      onClose()
    } catch (err) {
      setSubmitError(apiRefusalText(t, err, 'stockDetail.purchase.storno.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('stockDetail.purchase.storno.title')}
      onSubmit={submit}
      submitLabel={t('stockDetail.purchase.storno.submit')}
      cancelLabel={t('stockDetail.purchase.storno.cancel')}
      submitting={submitting}
    >
      <Stack gap="sm">
        <Text size="sm">{t('stockDetail.purchase.storno.body', { stockNumber: item.stockNumber })}</Text>
        <Textarea
          label={t('stockDetail.purchase.storno.reasonLabel')}
          value={reason}
          onChange={(e) => {
            setReason(e.currentTarget.value)
            if (reasonError) setReasonError(null)
          }}
          error={reasonError}
          maxLength={500}
          autosize
          minRows={2}
          maxRows={5}
          withAsterisk
          data-autofocus
        />
        {tradeIn && (
          <Checkbox
            {...hint(
              t('stockDetail.purchase.storno.returnedToCustomer'),
              t('stockDetail.purchase.storno.returnedToCustomerHint'),
              { descriptionId: returnedHintId },
            )}
            checked={returnedToCustomer}
            onChange={(e) => setReturnedToCustomer(e.currentTarget.checked)}
          />
        )}
        {submitError && (
          <Text size="xs" c="red" role="alert">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
