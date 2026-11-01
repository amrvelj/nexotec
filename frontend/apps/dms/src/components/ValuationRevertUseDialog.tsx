import { useEffect, useState } from 'react'
import { Stack, Text, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import type { ValuationRead } from '../api/types'
import { apiRefusalText } from '../utils/apiRefusal'

interface ValuationRevertUseDialogProps {
  opened: boolean
  onClose: () => void
  valuation: Pick<ValuationRead, 'id' | 'version' | 'valuationNumber'>
  onSaved: (updated: ValuationRead) => void
}

/**
 * KAN-253 (Anto, 2026-10-31, option C) — the ONLY place the SPA calls
 * `POST /v1/valuations/{id}/revert-use`: the dealer manager clears a
 * «Verwendet» stamp no signed contract stands behind, with a stated reason
 * (the API refuses a blank one; checked here first so the user is told
 * before the round trip). Reached from the shared row menu
 * (`buildValuationRowMenu`) on the list and the detail screen.
 */
export function ValuationRevertUseDialog({ opened, onClose, valuation, onSaved }: ValuationRevertUseDialogProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // KAN-266: a retried submit replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) {
      setReason('')
      setReasonError(null)
      setSubmitError(null)
      idempotency.renew()
    }
  }, [opened, idempotency])

  const submit = async () => {
    if (reason.trim().length === 0) {
      setReasonError(t('valuationDetail.revertUseDialog.reasonRequired'))
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const path = `/valuations/${valuation.id}/revert-use`
      const updated = await api.post<ValuationRead>(
        path,
        { reason: reason.trim() },
        { 'If-Match': String(valuation.version), ...idempotency.headers([path]) },
      )
      idempotency.renew()
      onSaved(updated)
      onClose()
    } catch (err) {
      setSubmitError(apiRefusalText(t, err, 'valuationDetail.revertUseDialog.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('valuationDetail.revertUseDialog.title')}
      onSubmit={submit}
      submitLabel={t('valuationDetail.revertUseDialog.submit')}
      cancelLabel={t('valuationDetail.revertUseDialog.cancel')}
      submitting={submitting}
    >
      <Stack gap="sm">
        <Text size="sm">{t('valuationDetail.revertUseDialog.body', { number: valuation.valuationNumber })}</Text>
        <Textarea
          label={t('valuationDetail.revertUseDialog.reasonLabel')}
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
        {submitError && (
          <Text size="xs" c="red" role="alert">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
