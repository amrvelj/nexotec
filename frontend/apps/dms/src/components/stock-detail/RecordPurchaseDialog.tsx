import { useEffect, useState } from 'react'
import { Alert, Checkbox, NumberInput, Stack, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'

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
 *
 * KAN-272 (ADR-035): a failed save says so and keeps the dialog open with
 * what was entered, so the retry — under the same Idempotency-Key — is one
 * click away.
 */
export function RecordPurchaseDialog({ opened, onClose, onSubmit }: RecordPurchaseDialogProps) {
  const { t } = useTranslation()
  const [supplierName, setSupplierName] = useState('')
  const [supplierIsVatRegistered, setSupplierIsVatRegistered] = useState(false)
  const [purchasePrice, setPurchasePrice] = useState<number | ''>('')
  const [purchaseDate, setPurchaseDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [purchaseInvoiceRef, setPurchaseInvoiceRef] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (opened) setError(null)
  }, [opened])

  const submit = async () => {
    if (!supplierName || purchasePrice === '') return
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        supplierName,
        supplierIsVatRegistered,
        purchasePrice: Number(purchasePrice),
        purchaseDate,
        purchaseInvoiceRef: purchaseInvoiceRef || undefined,
      })
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('stockDetail.purchase.error'))
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
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
