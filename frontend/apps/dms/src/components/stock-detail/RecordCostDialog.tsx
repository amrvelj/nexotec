import { useEffect, useState } from 'react'
import { Alert, NumberInput, Select, Stack, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'
import { translatedLedgerCategoryOptions } from '../../stockOptions'
import type { LedgerCategory } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'

interface RecordCostDialogProps {
  opened: boolean
  onClose: () => void
  onSubmit: (data: { category: LedgerCategory; amount: number; occurredAt: string; sourceRef: string }) => Promise<void>
}

/**
 * WP-7 PR-6. `sourceRef` is client-generated — recordCost's own
 * idempotency key (services/ledger.py::record_cost). KAN-266: one per
 * request, not per click. A retry after a failure (a response lost on the
 * way back) resends the same `sourceRef`, so the ledger returns the entry
 * the first attempt booked; a corrected category, amount or date gets a new
 * one, so it is booked rather than answered with the first entry. Every
 * opening starts afresh.
 * Category options exclude the two automatic-only ones (verkaufserloes,
 * foerderung) — hand-booking them is refused server-side anyway, so
 * there's no reason to offer them here.
 */
export function RecordCostDialog({ opened, onClose, onSubmit }: RecordCostDialogProps) {
  const { t } = useTranslation()
  const [category, setCategory] = useState<LedgerCategory | ''>('')
  const [amount, setAmount] = useState<number | ''>('')
  const [occurredAt, setOccurredAt] = useState(() => new Date().toISOString().slice(0, 10))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const categoryOptions = translatedLedgerCategoryOptions(t)
  const sourceRefs = useIdempotencyKey()
  useEffect(() => {
    if (!opened) return
    sourceRefs.renew()
    setError(null)
  }, [opened, sourceRefs])

  const submit = async () => {
    if (!category || amount === '') return
    setSubmitting(true)
    setError(null)
    try {
      const entry = { category, amount: Number(amount), occurredAt: new Date(occurredAt).toISOString() }
      await onSubmit({ ...entry, sourceRef: sourceRefs.headers(entry)['Idempotency-Key'] })
      sourceRefs.renew()
      onClose()
      setCategory('')
      setAmount('')
    } catch (err) {
      // Shown, so the user can send it again: a retry carries the same sourceRef.
      setError(err instanceof ApiError ? err.message : t('stockDetail.wagenbuch.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('stockDetail.wagenbuch.dialogTitle')}
      onSubmit={submit}
      submitLabel={t('stockDetail.wagenbuch.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!category || amount === ''}
    >
      <Stack gap="md">
        <Select
          label={t('stockDetail.wagenbuch.fields.category')}
          data={categoryOptions.map((o) => ({ value: o.value, label: o.label }))}
          value={category}
          onChange={(v) => setCategory((v as LedgerCategory) ?? '')}
          required
        />
        <NumberInput
          label={t('stockDetail.wagenbuch.fields.amount')}
          value={amount}
          onChange={(v) => setAmount(typeof v === 'number' ? v : '')}
          required
        />
        <TextInput
          type="date"
          label={t('stockDetail.wagenbuch.fields.occurredAt')}
          value={occurredAt}
          onChange={(e) => setOccurredAt(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
