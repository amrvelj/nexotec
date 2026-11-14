import { useEffect, useState } from 'react'
import { Alert, Button, Group, Modal, NumberInput, Stack, Text, TextInput, Textarea, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog, Picker, type PickerRow } from '@nexotec/ui-kit'
import { ApiError, api } from '../../../api/client'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { CustomerCreateDialog } from '../../customer'
import { ConfigurationSummaryCard } from '../../../components/configurator/ConfigurationSummaryCard'
import { ProviderQuoteCard } from './ProviderQuoteCard'
import { ValuationSourceMarker } from './ValuationSourceMarker'
import type {
  CapabilityCheckRead,
  ConfigurationRead,
  CustomerPage,
  CustomerRead,
  ProviderQuoteRead,
  ProviderQuoteRequest,
  ValuationCreate,
  ValuationRead,
} from '../../../api/types'
import { formatCurrencyChf } from '../../../utils/format'
import { vinRefusalKey } from '../../../utils/vin'
import { useFieldHint } from '../../../hooks/useFieldHint'
import { apiRefusalText } from '../../../utils/apiRefusal'

export interface ValuationCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (valuation: ValuationRead) => void
  /** "Neu bewerten" prefill — the row this new valuation supersedes. Its
   * vehicle/customer facts are copied across; its own value figures are
   * NOT (a revaluation is a fresh opinion, not a duplicate). */
  supersedes?: ValuationRead | null
  /** C-F (KAN-10, FR-C-14) — the `record`-mode configuration the car was
   * just captured in. Its facts prefill the vehicle section and it travels
   * as `configurationId`: the backend then creates **no** vehicle-mdm record
   * (ADR-070, amending FR-V-17). */
  configuration?: ConfigurationRead | null
  /** KAN-468 (Anto's option A) — «Request valuation» on a vehicle: its VIN,
   * first registration and its catalogue variant's make, model and trim
   * prefill the vehicle section when the car was not captured in the
   * configurator (the captured configuration wins). */
  vehicle?: ValuationVehiclePrefill | null
}

export interface ValuationVehiclePrefill {
  vin: string
  firstRegistrationDate: string | null
  make: string | null
  model: string | null
  trim: string | null
}

const DEDUCTIONS_EMPTY: { label: string; amount: string }[] = []

// KAN-552 — the quote's refusals in this dialog's own words («nicht im
// Vertrag», «derzeit nicht verfügbar»); anything else through apiRefusalText.
const QUOTE_REFUSALS: Record<string, string> = {
  not_entitled: 'valuationCreate.quote.notInContract',
  no_provider_connection: 'valuationCreate.quote.noConnection',
  provider_unavailable: 'valuationCreate.quote.unavailable',
  no_catalogue_variant: 'valuationCreate.quote.noCatalogueVariant',
}

/**
 * WP-8 PR-9 — the standalone application's own create dialog. Deductions
 * are a plain local array here, not RepeatableRowGroup: that component's
 * own shape (type/value/label/isPrimary/consent, each row independently
 * persisted via its own PATCH) is built for ADR-067 contact channels
 * specifically — a deduction is a create-time-only {label, amount} pair
 * with no type, no consent, and no row of its own until the whole
 * valuation is submitted. Forcing it through RepeatableRowGroup would fit
 * neither its UI nor its async per-row persistence model; a small local
 * editor is the honest choice here, not a shortcut around a real gap.
 */
export function ValuationCreateDialog({
  opened,
  onClose,
  onCreated,
  supersedes,
  configuration = null,
  vehicle = null,
}: ValuationCreateDialogProps) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  // KAN-266: one key per submission — a retry after a failure gets the
  // first answer back; another valuation, or another opening, gets a new key.
  // A success does not renew it: both hosts close the dialog once they have
  // used the valuation, and it stays open only while the offer's trade-in
  // attach is still running or after it failed. "Erstellen" again then
  // replays the valuation already created instead of creating a second one.
  const idempotency = useIdempotencyKey()
  // One key per quote request: a retry after a lost answer replays the
  // stored quote instead of asking the provider again.
  const quoteKey = useIdempotencyKey()
  useEffect(() => {
    if (opened) {
      idempotency.renew()
      quoteKey.renew()
    }
  }, [opened, idempotency, quoteKey])

  const c = configuration
  // KAN-449 (Anto, 2026-10-25): a configuration that names a car (by VIN or
  // linked record) gives the valuation its VIN — shown read-only, never sent
  // beside it. One with no VIN links no car, so the VIN stays free to type.
  const vinFromConfiguration = Boolean(c && (c.vin || c.vehicleId))
  const [vin, setVin] = useState(c?.vin ?? vehicle?.vin ?? supersedes?.vehicleVin ?? '')
  const [make, setMake] = useState(c?.brandDisplayName ?? vehicle?.make ?? supersedes?.vehicleMake ?? '')
  const [model, setModel] = useState(c?.modelGroupName ?? vehicle?.model ?? supersedes?.vehicleModel ?? '')
  const [trim, setTrim] = useState(c?.variantName ?? vehicle?.trim ?? supersedes?.vehicleTrim ?? '')
  const [plate, setPlate] = useState(c?.licencePlate ?? supersedes?.vehiclePlate ?? '')
  const [mileage, setMileage] = useState<number | ''>(c?.mileageKm ?? supersedes?.mileage ?? '')
  // KAN-353: a car first registered before 1981 may carry a chassis number in
  // the VIN field; the API accepts it only with that first registration.
  const [firstRegistration, setFirstRegistration] = useState(
    c?.firstRegistrationDate ?? vehicle?.firstRegistrationDate ?? supersedes?.vehicleFirstRegistration ?? '',
  )

  const [customerId, setCustomerId] = useState<string | null>(supersedes?.customerId ?? null)
  const [customerLabel, setCustomerLabel] = useState<string | null>(supersedes?.customerLabel ?? null)
  const [customerPickerOpen, setCustomerPickerOpen] = useState(false)
  const [customerCreateOpen, setCustomerCreateOpen] = useState(false)
  const [customerQuery, setCustomerQuery] = useState('')
  const [debouncedCustomerQuery] = useDebouncedValue(customerQuery, 250)
  const customerSearch = useQuery({
    queryKey: ['customer-search', debouncedCustomerQuery],
    queryFn: () => api.get<CustomerPage>(`/customers?q=${encodeURIComponent(debouncedCustomerQuery)}&limit=10`),
    enabled: customerPickerOpen && debouncedCustomerQuery.length > 0,
  })

  const [providerValue, setProviderValue] = useState<number | ''>('')
  // KAN-552 — the identAPI's answer, fetched only on the advisor's click
  // (never on open) and stored server-side; the valuation references it.
  const [quote, setQuote] = useState<ProviderQuoteRead | null>(null)
  const [quoting, setQuoting] = useState(false)
  const [quoteError, setQuoteError] = useState<string | null>(null)
  const [finalOffer, setFinalOffer] = useState<number | ''>('')
  const [validForDays, setValidForDays] = useState(30) // Q-11, confirmed live default
  const [note, setNote] = useState('')
  const [deductions, setDeductions] = useState(DEDUCTIONS_EMPTY)
  const [newDeductionLabel, setNewDeductionLabel] = useState('')
  const [newDeductionAmount, setNewDeductionAmount] = useState<number | ''>('')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-351 — a VIN the API refuses (422 on `vin`) is said at the VIN input,
  // in the user's language, not as the API's English message.
  const [vinError, setVinError] = useState<string | null>(null)

  // WP-6 PR-5 — a tenant with no auto-i-dat Bewertung capability (no
  // contract at all, or a declared restriction) still gets a fully usable
  // dialog: `source` already defaults to "manual" regardless, this banner
  // just explains why the provider option won't return a value.
  const bewertungCapability = useQuery({
    queryKey: ['capability-check', 'valuation'],
    queryFn: () => api.get<CapabilityCheckRead>('/integrations/capabilities/valuation'),
  })
  const bewertungUnavailable = bewertungCapability.data?.granted === false
  // The button needs the option granted (read strictly: a connection never
  // tested is not granted) and a car the provider knows by its FzKey.
  const canQuote = Boolean(c?.catalogueVariantId) && bewertungCapability.data?.granted === true
  const quoteInputsReady = firstRegistration !== '' && mileage !== ''
  // A quote values what the dialog showed when it was fetched; changed
  // figures need a new one (the API refuses the old one: provider_quote_stale).
  const quoteStale = quote != null && (quote.mileage !== mileage || quote.firstRegistration !== firstRegistration)
  const activeQuote = quote != null && !quoteStale ? quote : null
  // A stale quote's figure is not the advisor's: once the quote stops
  // applying, its trade-in price leaves the field rather than being sent as
  // a manual figure with no quote beside it.
  useEffect(() => {
    if (quoteStale && quote && providerValue !== '' && Number(providerValue) === Number(quote.exchangePrice)) {
      setProviderValue('')
    }
  }, [quoteStale, quote, providerValue])
  const derivedSource =
    activeQuote && (providerValue === '' || Number(providerValue) === Number(activeQuote.exchangePrice)) ? 'auto_i_dat' : 'manual'

  const fetchQuote = async () => {
    if (!c || !quoteInputsReady) return
    setQuoting(true)
    setQuoteError(null)
    try {
      const body: ProviderQuoteRequest = {
        configurationId: c.id,
        // Both filled: `quoteInputsReady` above narrows them.
        firstRegistration,
        mileage,
      }
      const fetched = await api.post<ProviderQuoteRead>('/valuations/provider-quotes', body, quoteKey.headers(body))
      quoteKey.renew()
      setQuote(fetched)
      setProviderValue(Number(fetched.exchangePrice))
    } catch (err) {
      const reason = err instanceof ApiError ? (err.details?.reason as string | undefined) : undefined
      setQuoteError(reason && QUOTE_REFUSALS[reason] ? t(QUOTE_REFUSALS[reason]) : apiRefusalText(t, err, 'valuationCreate.quote.failed'))
    } finally {
      setQuoting(false)
    }
  }

  const addDeduction = () => {
    if (!newDeductionLabel || newDeductionAmount === '') return
    setDeductions((prev) => [...prev, { label: newDeductionLabel, amount: String(newDeductionAmount) }])
    setNewDeductionLabel('')
    setNewDeductionAmount('')
  }
  const removeDeduction = (index: number) => setDeductions((prev) => prev.filter((_, i) => i !== index))

  const submit = async () => {
    if (finalOffer === '') return
    setSubmitting(true)
    setError(null)
    setVinError(null)
    try {
      const body: ValuationCreate = {
        vin: vinFromConfiguration ? null : vin || null,
        vehicleMake: make || null,
        vehicleModel: model || null,
        vehicleTrim: trim || null,
        vehiclePlate: plate || null,
        mileage: mileage === '' ? null : mileage,
        customerId,
        // KAN-552: the server derives the source from the quote.
        providerQuoteId: activeQuote?.id ?? null,
        providerValue: providerValue === '' ? null : String(providerValue),
        finalOffer: String(finalOffer),
        deductions,
        note: note || null,
        validForDays,
        supersedesValuationId: supersedes?.id ?? null,
        configurationId: configuration?.id ?? null,
        vehicleFirstRegistration: firstRegistration || null,
      }
      const created = await api.post<ValuationRead>('/valuations', body, idempotency.headers(body))
      onCreated(created)
    } catch (err) {
      const vinRefusal = vinRefusalKey(err)
      if (vinRefusal) setVinError(t(vinRefusal))
      else setError(apiRefusalText(t, err, 'valuationCreate.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <FormDialog
        opened={opened}
        onClose={onClose}
        title={supersedes ? t('valuationCreate.revalueTitle', { number: supersedes.valuationNumber }) : t('valuationCreate.title')}
        onSubmit={submit}
        submitLabel={t('valuationCreate.submit')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
        // KAN-574 (Anto, 2026-11-07, option A): a valuation names its car —
        // VIN (or a pre-1981 chassis number), make and model are required.
        submitDisabled={finalOffer === '' || !vin.trim() || !make.trim() || !model.trim()}
      >
        <Stack gap="md">
          {/* Confirmed live, verbatim. */}
          <Text size="sm" c="dimmed">{t('valuationCreate.banner')}</Text>
          {bewertungUnavailable && <Alert color="yellow">{t('valuationCreate.noProviderCapability')}</Alert>}
          {error && <Alert color="red">{error}</Alert>}

          <Stack gap="xs">
            <Text size="sm" fw={600}>{t('valuationCreate.vehicleSection')}</Text>
            {configuration && <ConfigurationSummaryCard configuration={configuration} />}
            <Group grow>
              <TextInput
                {...(vinFromConfiguration
                  ? hint(t('valuationCreate.vin'), t('valuationCreate.vinFromConfigurationHint'))
                  : { label: t('valuationCreate.vin') })}
                value={vin}
                required
                readOnly={vinFromConfiguration}
                onChange={(e) => {
                  setVin(e.currentTarget.value.toUpperCase())
                  setVinError(null)
                }}
                error={vinError}
              />
              <TextInput
                label={t('valuationCreate.firstRegistration')}
                type="date"
                value={firstRegistration}
                onChange={(e) => {
                  setFirstRegistration(e.currentTarget.value)
                  setVinError(null)
                }}
              />
              <NumberInput label={t('valuationCreate.mileage')} value={mileage} onChange={(v) => setMileage(v === '' ? '' : Number(v))} />
            </Group>
            <Group grow>
              <TextInput label={t('valuationCreate.make')} required value={make} onChange={(e) => setMake(e.currentTarget.value)} />
              <TextInput label={t('valuationCreate.model')} required value={model} onChange={(e) => setModel(e.currentTarget.value)} />
              <TextInput label={t('valuationCreate.trim')} value={trim} onChange={(e) => setTrim(e.currentTarget.value)} />
            </Group>
            <TextInput label={t('valuationCreate.plate')} value={plate} onChange={(e) => setPlate(e.currentTarget.value)} />
          </Stack>

          <Stack gap="xs">
            <Text size="sm" fw={600}>{t('valuationCreate.customerSection')}</Text>
            {customerId ? (
              <Group justify="space-between">
                <Text size="sm">{customerLabel}</Text>
                <Button variant="subtle" size="xs" onClick={() => { setCustomerId(null); setCustomerLabel(null) }}>
                  {t('common.remove')}
                </Button>
              </Group>
            ) : (
              <Group gap="xs">
                <Button variant="default" size="xs" onClick={() => setCustomerPickerOpen(true)}>
                  {t('valuationCreate.searchCustomer')}
                </Button>
                <Button variant="subtle" size="xs" onClick={() => setCustomerCreateOpen(true)}>
                  {t('valuationCreate.createCustomer')}
                </Button>
              </Group>
            )}
          </Stack>

          {canQuote && (
            <Stack gap="xs" data-testid="provider-quote-section">
              <Group justify="space-between">
                <Text size="sm" fw={600}>{t('valuationQuote.title')}</Text>
                <Button
                  size="xs"
                  variant={quote ? 'default' : 'filled'}
                  loading={quoting}
                  disabled={!quoteInputsReady}
                  onClick={() => void fetchQuote()}
                >
                  {quote ? t('valuationCreate.quote.refetch') : t('valuationCreate.quote.fetch')}
                </Button>
              </Group>
              {!quoteInputsReady && <Text size="xs" c="dimmed">{t('valuationCreate.quote.needsInputs')}</Text>}
              {quoteError && <Alert color="yellow">{quoteError}</Alert>}
              {quoteStale && <Alert color="yellow">{t('valuationCreate.quote.stale')}</Alert>}
              {activeQuote && <ProviderQuoteCard quote={activeQuote} />}
            </Stack>
          )}

          <Group grow align="flex-end">
            <Stack gap={4}>
              <Text size="sm" fw={500}>{t('valuationCreate.source')}</Text>
              <ValuationSourceMarker source={derivedSource} />
            </Stack>
            <ChfInput
              label={t('valuationCreate.providerValue')}
              value={providerValue}
              onChange={(v) => setProviderValue(v === '' ? '' : Number(v))}
            />
            <NumberInput label={t('valuationCreate.validForDays')} value={validForDays} onChange={(v) => setValidForDays(v === '' ? 30 : Number(v))} />
          </Group>

          <Stack gap="xs">
            <Text size="sm" fw={600}>{t('valuationCreate.deductionsSection')}</Text>
            {deductions.map((d, i) => (
              <Group key={i} justify="space-between">
                <Text size="sm">{d.label}</Text>
                <Group gap="xs">
                  <Text size="sm">− {formatCurrencyChf(Number(d.amount))}</Text>
                  <UnstyledButton onClick={() => removeDeduction(i)} aria-label={t('common.remove')}>
                    <Trash2 size={14} />
                  </UnstyledButton>
                </Group>
              </Group>
            ))}
            <Group gap="xs" align="flex-end">
              <TextInput
                size="xs"
                label={t('valuationCreate.deductionLabel')}
                value={newDeductionLabel}
                onChange={(e) => setNewDeductionLabel(e.currentTarget.value)}
                style={{ flex: 1 }}
              />
              <ChfInput
                size="xs"
                label={t('valuationCreate.deductionAmount')}
                value={newDeductionAmount}
                onChange={(v) => setNewDeductionAmount(v === '' ? '' : Number(v))}
                style={{ width: 120 }}
              />
              <Button size="xs" onClick={addDeduction} disabled={!newDeductionLabel || newDeductionAmount === ''}>
                {t('valuationCreate.addDeduction')}
              </Button>
            </Group>
          </Stack>

          <Stack gap={2}>
            <ChfInput label={t('valuationCreate.finalOffer')} value={finalOffer} onChange={(v) => setFinalOffer(v === '' ? '' : Number(v))} required />
            <Text size="xs" c="dimmed">{t('valuationCreate.finalOfferHint')}</Text>
          </Stack>

          <Textarea label={t('valuationCreate.note')} value={note} onChange={(e) => setNote(e.currentTarget.value)} autosize minRows={2} />
        </Stack>
      </FormDialog>

      <Modal opened={customerPickerOpen} onClose={() => setCustomerPickerOpen(false)} title={t('valuationCreate.searchCustomer')}>
        <Picker
          rows={(customerSearch.data?.items ?? []).map(
            (c: CustomerRead): PickerRow => ({
              id: c.id,
              identifier: c.customerNumber,
              label: c.companyName ?? [c.firstName, c.lastName].filter(Boolean).join(' '),
            })
          )}
          query={customerQuery}
          onQueryChange={setCustomerQuery}
          onSelect={(row) => {
            setCustomerId(row.id)
            setCustomerLabel(row.label)
            setCustomerPickerOpen(false)
          }}
          loading={customerSearch.isFetching}
          placeholder={t('valuationCreate.searchCustomer')}
          emptyLabel={t('common.noMatches')}
        />
      </Modal>

      <CustomerCreateDialog
        opened={customerCreateOpen}
        onClose={() => setCustomerCreateOpen(false)}
        onCreated={(customer) => {
          setCustomerId(customer.id)
          setCustomerLabel(customer.companyName ?? [customer.firstName, customer.lastName].filter(Boolean).join(' '))
          setCustomerCreateOpen(false)
        }}
      />
    </>
  )
}
