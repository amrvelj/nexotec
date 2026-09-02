import { useState } from 'react'
import { Alert, Button, Group, Modal, NumberInput, Select, Stack, Text, TextInput, Textarea, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog, Picker, type PickerRow } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { CustomerCreateFlow } from './CustomerCreateFlow'
import type { CapabilityCheckRead, CustomerPage, CustomerRead, ValuationCreate, ValuationRead, ValuationSourceValue } from '../api/types'

export interface ValuationCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (valuation: ValuationRead) => void
  /** "Neu bewerten" prefill — the row this new valuation supersedes. Its
   * vehicle/customer facts are copied across; its own value figures are
   * NOT (a revaluation is a fresh opinion, not a duplicate). */
  supersedes?: ValuationRead | null
}

const DEDUCTIONS_EMPTY: { label: string; amount: string }[] = []

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
export function ValuationCreateDialog({ opened, onClose, onCreated, supersedes }: ValuationCreateDialogProps) {
  const { t } = useTranslation()

  const [vin, setVin] = useState(supersedes?.vehicleVin ?? '')
  const [make, setMake] = useState(supersedes?.vehicleMake ?? '')
  const [model, setModel] = useState(supersedes?.vehicleModel ?? '')
  const [trim, setTrim] = useState(supersedes?.vehicleTrim ?? '')
  const [plate, setPlate] = useState(supersedes?.vehiclePlate ?? '')
  const [mileage, setMileage] = useState<number | ''>(supersedes?.mileage ?? '')

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

  const [source, setSource] = useState<ValuationSourceValue>('manual')
  const [providerValue, setProviderValue] = useState<number | ''>('')
  const [finalOffer, setFinalOffer] = useState<number | ''>('')
  const [validForDays, setValidForDays] = useState(30) // Q-11, confirmed live default
  const [note, setNote] = useState('')
  const [deductions, setDeductions] = useState(DEDUCTIONS_EMPTY)
  const [newDeductionLabel, setNewDeductionLabel] = useState('')
  const [newDeductionAmount, setNewDeductionAmount] = useState<number | ''>('')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // WP-6 PR-5 — a tenant with no auto-i-dat Bewertung capability (no
  // contract at all, or a declared restriction) still gets a fully usable
  // dialog: `source` already defaults to "manual" regardless, this banner
  // just explains why the provider option won't return a value.
  const bewertungCapability = useQuery({
    queryKey: ['capability-check', 'valuation'],
    queryFn: () => api.get<CapabilityCheckRead>('/integrations/capabilities/valuation'),
  })
  const bewertungUnavailable = bewertungCapability.data?.granted === false

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
    try {
      const body: ValuationCreate = {
        vin: vin || null,
        vehicleMake: make || null,
        vehicleModel: model || null,
        vehicleTrim: trim || null,
        vehiclePlate: plate || null,
        mileage: mileage === '' ? null : mileage,
        customerId,
        source,
        providerValue: providerValue === '' ? null : String(providerValue),
        finalOffer: String(finalOffer),
        deductions,
        note: note || null,
        validForDays,
        supersedesValuationId: supersedes?.id ?? null,
      }
      const created = await api.post<ValuationRead>('/valuations', body)
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('valuationCreate.error'))
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
        submitDisabled={finalOffer === ''}
      >
        <Stack gap="md">
          {/* Confirmed live, verbatim. */}
          <Text size="sm" c="dimmed">{t('valuationCreate.banner')}</Text>
          {bewertungUnavailable && <Alert color="yellow">{t('valuationCreate.noProviderCapability')}</Alert>}
          {error && <Alert color="red">{error}</Alert>}

          <Stack gap="xs">
            <Text size="sm" fw={600}>{t('valuationCreate.vehicleSection')}</Text>
            <Group grow>
              <TextInput label={t('valuationCreate.vin')} value={vin} onChange={(e) => setVin(e.currentTarget.value.toUpperCase())} />
              <NumberInput label={t('valuationCreate.mileage')} value={mileage} onChange={(v) => setMileage(v === '' ? '' : Number(v))} />
            </Group>
            <Group grow>
              <TextInput label={t('valuationCreate.make')} value={make} onChange={(e) => setMake(e.currentTarget.value)} />
              <TextInput label={t('valuationCreate.model')} value={model} onChange={(e) => setModel(e.currentTarget.value)} />
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

          <Group grow align="flex-end">
            <Select
              label={t('valuationCreate.source')}
              data={[
                { value: 'auto_i_dat', label: 'auto-i-dat', disabled: bewertungUnavailable },
                { value: 'manual', label: t('valuationCreate.sourceManual') },
              ]}
              value={bewertungUnavailable ? 'manual' : source}
              onChange={(v) => setSource((v as ValuationSourceValue) ?? 'manual')}
            />
            <NumberInput
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
                  <Text size="sm">− {d.amount} CHF</Text>
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
              <NumberInput
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
            <NumberInput label={t('valuationCreate.finalOffer')} value={finalOffer} onChange={(v) => setFinalOffer(v === '' ? '' : Number(v))} required />
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
        />
      </Modal>

      <Modal opened={customerCreateOpen} onClose={() => setCustomerCreateOpen(false)} title={t('valuationCreate.createCustomer')} size="lg">
        <CustomerCreateFlow
          onSuccess={(customer) => {
            setCustomerId(customer.id)
            setCustomerLabel(customer.companyName ?? [customer.firstName, customer.lastName].filter(Boolean).join(' '))
            setCustomerCreateOpen(false)
          }}
          onCancel={() => setCustomerCreateOpen(false)}
        />
      </Modal>
    </>
  )
}
