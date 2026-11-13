import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Stack, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { ConfigurationRead } from '../../api/types'
import { todayLocalIsoDate } from '../../utils/format'
import { apiRefusalText } from '../../utils/apiRefusal'
import { vinRefusalKey } from '../../utils/vin'
import { useFieldHint } from '../../hooks/useFieldHint'

interface RecordPurchaseDialogProps {
  opened: boolean
  onClose: () => void
  /** KAN-446: a pipeline item is bought with its VIN, which puts it on stock. */
  requiresVin?: boolean
  /** KAN-510: the item's configuration. Its VIN prefills the field, and the
   * booking refuses another (422 `configuration_vin_differs`). */
  configurationId?: string | null
  /** KAN-536: the VIN the contract holds for a trade-in (the item's
   * `tradeInVin`). It prefills the field when the configuration carries
   * none, and the booking refuses another (422 `trade_in_vin_differs`). */
  tradeInVin?: string | null
  /** KAN-510: offered beside that refusal — opens the configuration for
   * editing, where a wrong VIN is corrected. Omitted when the user's role
   * may not write configurations. */
  onEditConfiguration?: (configuration: ConfigurationRead) => void
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
 *
 * KAN-510 (Anto, 2026-11-01): the VIN is prefilled from the item's
 * configuration when it carries one (a car recorded with its VIN), read as
 * every host reads it (`['configuration', id]`, the card's query), and
 * follows it when the configuration is corrected. A different VIN is
 * refused by the server; the refusal says so and offers «Konfiguration
 * bearbeiten». A configuration without a VIN (a factory order) leaves the
 * field to the user, as before.
 *
 * KAN-536 (Anto, 2026-11-12, option A): a trade-in's pipeline item knows the
 * VIN its contract holds. It prefills the field when no configuration VIN
 * does (the configuration's decides when there is one, as on the server); a
 * different VIN is refused by the server and said in the UI language
 * (`apiRefusal.reasons.trade_in_vin_differs`). The signed contract is
 * frozen, so that refusal offers no edit.
 */
export function RecordPurchaseDialog({
  opened,
  onClose,
  requiresVin = false,
  configurationId = null,
  tradeInVin = null,
  onEditConfiguration,
  onSubmit,
}: RecordPurchaseDialogProps) {
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
  const [vinDiffers, setVinDiffers] = useState(false)

  const configurationQuery = useQuery({
    queryKey: ['configuration', configurationId],
    queryFn: () => api.get<ConfigurationRead>(`/configurations/${configurationId}`),
    enabled: requiresVin && configurationId != null,
  })
  const configuration = configurationQuery.data ?? null
  const configurationVin = configuration?.vin ?? null

  const knownVin = configurationVin ?? (requiresVin ? tradeInVin : null)

  useEffect(() => {
    if (knownVin) setVin(knownVin)
  }, [knownVin])

  useEffect(() => {
    if (opened) {
      setError(null)
      setVinDiffers(false)
    }
  }, [opened])

  const incomplete = !supplierName || purchasePrice === '' || (requiresVin && !vin.trim())

  const submit = async () => {
    if (incomplete) return
    setSubmitting(true)
    setError(null)
    setVinDiffers(false)
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
      if (err instanceof ApiError && err.status === 422 && err.details?.reason === 'configuration_vin_differs') {
        setVinDiffers(true)
        setError(t('stockDetail.purchase.configurationVinDiffers'))
        return
      }
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
            <Stack gap="xs" align="flex-start">
              <span>{error}</span>
              {vinDiffers && configuration && onEditConfiguration && (
                <Button variant="default" size="xs" onClick={() => onEditConfiguration(configuration)}>
                  {t('stockDetail.configuration.edit')}
                </Button>
              )}
            </Stack>
          </Alert>
        )}
      </Stack>
    </FormDialog>
  )
}
