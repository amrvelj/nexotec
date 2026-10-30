import { useEffect, useState } from 'react'
import { Alert, Checkbox, Stack, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog } from '@nexotec/ui-kit'
import { todayLocalIsoDate } from '../../utils/format'
import { apiRefusalText } from '../../utils/apiRefusal'
import { vinRefusalKey } from '../../utils/vin'
import { useFieldHint } from '../../hooks/useFieldHint'

interface RecordPurchaseDialogProps {
  opened: boolean
  onClose: () => void
  /** KAN-446: a pipeline item is bought with its VIN, which puts it on stock. */
  requiresVin?: boolean
  onSubmit: (data: {
    supplierName: string
    supplierIsVatRegistered: boolean
    purchasePrice: number
    purchaseDate: string
    purchaseInvoiceRef?: string
    vin?: string
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
 *
 * KAN-446 (ADR-045, FR-V-04): for a pipeline item the VIN is required — the
 * booking promotes the item into stock with it. The API normalises it; a
 * malformed one (a schema 422 on `vin`) and one already in stock are said in
 * the UI language.
 */
export function RecordPurchaseDialog({ opened, onClose, requiresVin = false, onSubmit }: RecordPurchaseDialogProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const [supplierName, setSupplierName] = useState('')
  const [supplierIsVatRegistered, setSupplierIsVatRegistered] = useState(false)
  const [purchasePrice, setPurchasePrice] = useState<number | ''>('')
  const [purchaseDate, setPurchaseDate] = useState(() => todayLocalIsoDate())
  const [purchaseInvoiceRef, setPurchaseInvoiceRef] = useState('')
  const [vin, setVin] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (opened) setError(null)
  }, [opened])

  const incomplete = !supplierName || purchasePrice === '' || (requiresVin && !vin.trim())

  const submit = async () => {
    if (incomplete) return
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        supplierName,
        supplierIsVatRegistered,
        purchasePrice: Number(purchasePrice),
        purchaseDate,
        purchaseInvoiceRef: purchaseInvoiceRef || undefined,
        ...(requiresVin ? { vin: vin.trim() } : {}),
      })
      onClose()
    } catch (err) {
      // A VIN the API refuses (KAN-351) is named by its rule, never as a
      // generic «some entries are invalid».
      const vinRefusal = vinRefusalKey(err)
      setError(vinRefusal ? t(vinRefusal) : apiRefusalText(t, err, 'stockDetail.purchase.error'))
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
      submitDisabled={incomplete}
    >
      <Stack gap="md">
        {requiresVin && (
          <TextInput
            {...hint(t('stockDetail.purchase.fields.vin'), t('stockDetail.purchase.fields.vinHint'))}
            value={vin}
            onChange={(e) => setVin(e.currentTarget.value)}
            required
          />
        )}
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
        <ChfInput
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
