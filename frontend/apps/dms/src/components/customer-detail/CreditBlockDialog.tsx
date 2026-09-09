import { useEffect, useState } from 'react'
import { Stack, Text, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { CustomerRead } from '../../api/types'

interface CreditBlockDialogProps {
  opened: boolean
  onClose: () => void
  customer: Pick<CustomerRead, 'id' | 'version' | 'customerNumber' | 'creditBlock' | 'creditBlockReason'>
  onSaved: (updated: CustomerRead) => void
}

/**
 * KAN-44 / ADR-065 (S-D19) — the ONLY place the SPA calls
 * `POST /v1/customers/{id}/credit-block`. Set requires a reason (the API
 * raises 409 without one — mirrored here as a client-side check so the
 * user is told before the round trip); clearing sends `reason: null`.
 * Reached from the shared row menu (`buildCustomerRowMenu`), never a
 * sixth header button.
 */
export function CreditBlockDialog({ opened, onClose, customer, onSaved }: CreditBlockDialogProps) {
  const { t } = useTranslation()
  const isBlocked = customer.creditBlock
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (opened) {
      setReason('')
      setReasonError(null)
      setSubmitError(null)
    }
  }, [opened])

  const submit = async () => {
    if (!isBlocked && reason.trim().length === 0) {
      setReasonError(t('creditBlockDialog.reasonRequired'))
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const updated = await api.post<CustomerRead>(
        `/customers/${customer.id}/credit-block`,
        { blocked: !isBlocked, reason: isBlocked ? null : reason.trim() },
        { 'If-Match': String(customer.version) },
      )
      onSaved(updated)
      onClose()
    } catch (err) {
      setSubmitError(
        err instanceof ApiError && err.status === 409
          ? t('creditBlockDialog.conflict')
          : err instanceof Error
            ? err.message
            : t('creditBlockDialog.failed'),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={isBlocked ? t('creditBlockDialog.clearTitle') : t('creditBlockDialog.setTitle')}
      onSubmit={submit}
      submitLabel={isBlocked ? t('creditBlockDialog.clearSubmit') : t('creditBlockDialog.setSubmit')}
      cancelLabel={t('creditBlockDialog.cancel')}
      submitting={submitting}
    >
      <Stack gap="sm">
        {isBlocked ? (
          <Text size="sm">
            {t('creditBlockDialog.clearBody', { number: customer.customerNumber })}
          </Text>
        ) : (
          <>
            <Text size="sm">{t('creditBlockDialog.setBody')}</Text>
            <Textarea
              label={t('creditBlockDialog.reasonLabel')}
              value={reason}
              onChange={(e) => {
                setReason(e.currentTarget.value)
                if (reasonError) setReasonError(null)
              }}
              error={reasonError}
              autosize
              minRows={2}
              maxRows={5}
              withAsterisk
              data-autofocus
            />
          </>
        )}
        {submitError && (
          <Text size="xs" c="red">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
