import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Group, Modal, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog, Picker, type PickerRow } from '@nexotec/ui-kit'
import { api, ApiError } from '../../../../api/client'
import type { ConfigurationRead, CustomerPage, CustomerRead } from '../../../../api/types'
import { CustomerCreateDialog } from '../../../customer'
import { supplierLabel, supplierVatPrefill } from './supplierContact'
import { todayLocalIsoDate } from '../../../../utils/format'
import { apiRefusalText } from '../../../../utils/apiRefusal'
import { vinRefusalKey } from '../../../../utils/vin'
import { useFieldHint } from '../../../../hooks/useFieldHint'

interface RecordPurchaseDialogProps {
  opened: boolean
  onClose: () => void
  /** KAN-446: a pipeline item is bought with its VIN, which puts it on stock. */
  requiresVin?: boolean
  /** KAN-510: the item's configuration. Its VIN prefills the field, and the
   * booking refuses another (422 `configuration_vin_differs`), and is where a
   * VIN naming a merged record is corrected (409 `vin_names_merged_vehicle`,
   * KAN-736). */
  configurationId?: string | null
  /** KAN-536: the VIN the contract holds for a trade-in (the item's
   * `tradeInVin`). It prefills the field when the configuration carries
   * none, and the booking refuses another (422 `trade_in_vin_differs`). */
  tradeInVin?: string | null
  /** KAN-465: the customer who traded the car in (the item's
   * `tradeInCustomerId`) — offered as the supplier with one click, never
   * preselected (the picker contract). */
  tradeInCustomerId?: string | null
  /** KAN-510, KAN-736: offered beside those refusals — opens the configuration for
   * editing, where a wrong VIN is corrected. Omitted when the user's role
   * may not write configurations. */
  onEditConfiguration?: (configuration: ConfigurationRead) => void
  onSubmit: (data: {
    supplierContactId: string
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
 *
 * KAN-465 (PRD-Stock: supplier = reference + label; Anto, 2026-11-21, option
 * A): the supplier is a contact, picked with the contact search or created
 * inline in the one create dialog with the supplier role preset. Picking a
 * contact without that role gives it the role on booking (a PATCH of the
 * contact, audited by Customers) before the purchase is booked. The contact's
 * VAT status prefills «Lieferant ist MWST-pflichtig» (`supplierVatPrefill`,
 * the server's rule); the user may change it, and the server audits the
 * override.
 */
export function RecordPurchaseDialog({
  opened,
  onClose,
  requiresVin = false,
  configurationId = null,
  tradeInVin = null,
  tradeInCustomerId = null,
  onEditConfiguration,
  onSubmit,
}: RecordPurchaseDialogProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const [supplier, setSupplier] = useState<CustomerRead | null>(null)
  const [supplierIsVatRegistered, setSupplierIsVatRegistered] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const search = useQuery({
    queryKey: ['customer-search', debouncedQuery],
    queryFn: () => api.get<CustomerPage>(`/customers?q=${encodeURIComponent(debouncedQuery)}&limit=10`),
    enabled: pickerOpen && debouncedQuery.length > 0,
  })
  const tradeInCustomer = useQuery({
    queryKey: ['customer', tradeInCustomerId],
    queryFn: () => api.get<CustomerRead>(`/customers/${tradeInCustomerId}`),
    enabled: opened && tradeInCustomerId != null,
  })
  const vatPrefill = supplier ? supplierVatPrefill(supplier) : null
  const lacksSupplierRole = supplier != null && !(supplier.roles ?? []).includes('supplier')

  const pickSupplier = (contact: CustomerRead) => {
    setSupplier(contact)
    const prefill = supplierVatPrefill(contact)
    if (prefill !== null) setSupplierIsVatRegistered(prefill)
  }
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

  const incomplete = supplier == null || purchasePrice === '' || (requiresVin && !vin.trim())

  const submit = async () => {
    if (incomplete) return
    setSubmitting(true)
    setError(null)
    setVinDiffers(false)
    let contact = supplier
    try {
      // KAN-465: a contact picked without the supplier role gets it first —
      // Customers' own write, audited there; a retry finds it held.
      if (contact && !(contact.roles ?? []).includes('supplier')) {
        contact = await api.patch<CustomerRead>(
          `/customers/${contact.id}`,
          { roles: [...(contact.roles ?? []), 'supplier'] },
          { 'If-Match': String(contact.version) },
        )
        setSupplier(contact)
      }
    } catch (err) {
      setError(apiRefusalText(t, err, 'stockDetail.purchase.supplier.roleFailed'))
      setSubmitting(false)
      return
    }
    if (!contact) return
    try {
      await onSubmit({
        supplierContactId: contact.id,
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
      // KAN-736 (Anto, 2026-11-15): a VIN only a merged duplicate carries is
      // refused naming the record the car lives on (KAN-723); here the text
      // ends with the way out — the configuration's VIN decides the booking
      // (KAN-575), so it is corrected there. The shared wording stays for
      // «VIN korrigieren», where the VIN is typed and this way out is none.
      if (err instanceof ApiError && err.status === 409 && err.details?.reason === 'vin_names_merged_vehicle') {
        const params = { vehicleNumber: String(err.details.vehicleNumber ?? '—'), vin: String(err.details.vin ?? '—') }
        setVinDiffers(configurationId != null)
        setError(
          t(
            configurationId != null
              ? 'stockDetail.purchase.vinNamesMergedVehicle'
              : 'stockDetail.purchase.vinNamesMergedVehicleNoConfiguration',
            params,
          ),
        )
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
    <>
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
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              {t('stockDetail.purchase.fields.supplierName')}
            </Text>
            {supplier ? (
              <Group gap="xs">
                <Text size="sm" data-testid="purchase-supplier">
                  {supplierLabel(supplier)} · {supplier.customerNumber}
                </Text>
                <Button variant="subtle" size="xs" onClick={() => setSupplier(null)}>
                  {t('stockDetail.purchase.supplier.change')}
                </Button>
              </Group>
            ) : (
              <Group gap="xs">
                <Button variant="default" size="xs" onClick={() => setPickerOpen(true)}>
                  {t('stockDetail.purchase.supplier.search')}
                </Button>
                <Button variant="subtle" size="xs" onClick={() => setCreateOpen(true)}>
                  {t('stockDetail.purchase.supplier.create')}
                </Button>
                {tradeInCustomer.data && (
                  <Button variant="subtle" size="xs" onClick={() => pickSupplier(tradeInCustomer.data)}>
                    {t('stockDetail.purchase.supplier.useTradeInCustomer', { name: supplierLabel(tradeInCustomer.data) })}
                  </Button>
                )}
              </Group>
            )}
            {lacksSupplierRole && (
              <Text size="xs" c="dimmed">
                {t('stockDetail.purchase.supplier.roleWillBeAdded')}
              </Text>
            )}
          </Stack>
          <Stack gap={4}>
            <Checkbox
              label={t('stockDetail.purchase.fields.supplierIsVatRegistered')}
              checked={supplierIsVatRegistered}
              onChange={(e) => setSupplierIsVatRegistered(e.currentTarget.checked)}
            />
            {supplier && (
              <Text size="xs" c="dimmed">
                {vatPrefill === null
                  ? t('stockDetail.purchase.supplier.vatUnknown')
                  : supplierIsVatRegistered === vatPrefill
                    ? t('stockDetail.purchase.supplier.vatFromContact')
                    : t('stockDetail.purchase.supplier.vatOverridden')}
              </Text>
            )}
          </Stack>
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
      <Modal opened={pickerOpen} onClose={() => setPickerOpen(false)} title={t('stockDetail.purchase.supplier.search')}>
        <Picker
          rows={(search.data?.items ?? []).map(
            (c: CustomerRead): PickerRow => ({ id: c.id, identifier: c.customerNumber, label: supplierLabel(c) }),
          )}
          query={query}
          onQueryChange={setQuery}
          onSelect={(row) => {
            const contact = search.data?.items.find((c) => c.id === row.id)
            if (contact) pickSupplier(contact)
            setPickerOpen(false)
          }}
          onCreateNew={() => {
            setPickerOpen(false)
            setCreateOpen(true)
          }}
          createLabel={t('stockDetail.purchase.supplier.create')}
          loading={search.isFetching}
          placeholder={t('stockDetail.purchase.supplier.search')}
          inputLabel={t('stockDetail.purchase.supplier.search')}
          emptyLabel={t('common.noMatches')}
        />
      </Modal>
      <CustomerCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        initialRoles={['supplier']}
        onCreated={(contact) => {
          pickSupplier(contact)
          setCreateOpen(false)
        }}
      />
    </>
  )
}
