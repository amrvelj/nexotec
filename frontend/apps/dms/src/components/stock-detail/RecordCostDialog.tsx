import { useState } from 'react'
import { NumberInput, Select, Stack, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { translatedLedgerCategoryOptions } from '../../stockOptions'
import type { LedgerCategory } from '../../api/types'

interface RecordCostDialogProps {
  opened: boolean
  onClose: () => void
  onSubmit: (data: { category: LedgerCategory; amount: number; occurredAt: string; sourceRef: string }) => Promise<void>
}

/**
 * WP-7 PR-6. `sourceRef` is client-generated (crypto.randomUUID()) —
 * recordCost's own idempotency key (services/ledger.py::record_cost),
 * same convention as reserve/release's Idempotency-Key header.
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

  const categoryOptions = translatedLedgerCategoryOptions(t)

  const submit = async () => {
    if (!category || amount === '') return
    setSubmitting(true)
    try {
      await onSubmit({
        category,
        amount: Number(amount),
        occurredAt: new Date(occurredAt).toISOString(),
        sourceRef: crypto.randomUUID(),
      })
      onClose()
      setCategory('')
      setAmount('')
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
      </Stack>
    </FormDialog>
  )
}
