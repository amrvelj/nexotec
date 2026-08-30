import { useState } from 'react'
import { Checkbox, NumberInput, Stack, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'

interface RecordPurchaseDialogProps {
  opened: boolean
  onClose: () => void
  onSubmit: (data: {
    supplierName: string
    supplierIsVatRegistered: boolean
    purchasePrice: number
    purchaseDate: string
    purchaseInvoiceRef?: string
  }) => Promise<void>
}

/**
 * WP-7 PR-3. `supplierIsVatRegistered` drives the backend's own
 * notional-input-tax prefill (Art. 28a MWSTG) — never computed here, this
 * dialog only collects the raw purchase facts.
 */
export function RecordPurchaseDialog({ opened, onClose, onSubmit }: RecordPurchaseDialogProps) {
  const { t } = useTranslation()
  const [supplierName, setSupplierName] = useState('')
  const [supplierIsVatRegistered, setSupplierIsVatRegistered] = useState(false)
  const [purchasePrice, setPurchasePrice] = useState<number | ''>('')
  const [purchaseDate, setPurchaseDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [purchaseInvoiceRef, setPurchaseInvoiceRef] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (!supplierName || purchasePrice === '') return
    setSubmitting(true)
    try {
      await onSubmit({
        supplierName,
        supplierIsVatRegistered,
        purchasePrice: Number(purchasePrice),
        purchaseDate,
        purchaseInvoiceRef: purchaseInvoiceRef || undefined,
      })
      onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('stockDetail.purchase.dialogTitle')}
      onSubmit={submit}
      submitLabel={t('stockDetail.purchase.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!supplierName || purchasePrice === ''}
    >
      <Stack gap="md">
        <TextInput
          label={t('stockDetail.purchase.fields.supplierName')}
          value={supplierName}
          onChange={(e) => setSupplierName(e.currentTarget.value)}
          required
        />
        <Checkbox
          label={t('stockDetail.purchase.fields.supplierIsVatRegistered')}
          checked={supplierIsVatRegistered}
          onChange={(e) => setSupplierIsVatRegistered(e.currentTarget.checked)}
        />
        <NumberInput
          label={t('stockDetail.purchase.fields.purchasePrice')}
          value={purchasePrice}
          onChange={(v) => setPurchasePrice(typeof v === 'number' ? v : '')}
          min={0}
          required
        />
        <TextInput
          type="date"
          label={t('stockDetail.purchase.fields.purchaseDate')}
          value={purchaseDate}
          onChange={(e) => setPurchaseDate(e.currentTarget.value)}
          required
        />
        <TextInput
          label={t('stockDetail.purchase.fields.purchaseInvoiceRef')}
          value={purchaseInvoiceRef}
          onChange={(e) => setPurchaseInvoiceRef(e.currentTarget.value)}
        />
      </Stack>
    </FormDialog>
  )
}
