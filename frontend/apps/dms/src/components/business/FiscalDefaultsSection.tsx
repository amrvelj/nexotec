import { useCallback, useState } from 'react'
import { NumberInput, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Calculator } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { ChartOfAccountsVariant, DealershipRead, DealershipSettingsRead, DealershipSettingsUpdate, PeriodLockPolicy } from '../../api/types'
import { CASH_ROUNDINGS, CHART_VARIANTS, DAY_OPTIONS, MONTHS, PERIOD_LOCK_POLICIES, businessRefusalText, joinFiscalYearStart, orNull, splitFiscalYearStart } from '../../businessOptions'
import { settingsKey } from './queryKeys'
import { SectionCard } from './SectionCard'
import { useDealershipPatch } from './useDealershipPatch'
import { useRecordForm } from './useRecordForm'

interface Form {
  currency: string
  cashRounding: string
  defaultPaymentTermsDays: number | string
  fiscalMonth: string
  fiscalDay: string
  accountingSystem: string
  mandant: string
  chartOfAccountsVariant: ChartOfAccountsVariant
  periodLockPolicy: PeriodLockPolicy
}

/**
 * Business › Commercial and fiscal defaults (KAN-203, FR-A-25): what every
 * document and module inherits. Every value ships with a neutral default,
 * so the form is never empty. The VAT rate is the dealership's own column
 * (ADR-057's one figure), edited here because this is the one
 * administration surface (A-RULE-0) — its meaning is untouched; it has its
 * own loaded record and If-Match.
 */
export function FiscalDefaultsSection({ dealership, settings }: { dealership: DealershipRead; settings: DealershipSettingsRead }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const toForm = useCallback(
    (s: DealershipSettingsRead): Form => {
      const { month, day } = splitFiscalYearStart(s.fiscalYearStart)
      return {
        currency: s.currency,
        cashRounding: String(s.cashRounding),
        defaultPaymentTermsDays: s.defaultPaymentTermsDays,
        fiscalMonth: month,
        fiscalDay: day,
        accountingSystem: s.accountingSystem ?? '',
        mandant: s.mandant ?? '',
        chartOfAccountsVariant: s.chartOfAccountsVariant,
        periodLockPolicy: s.periodLockPolicy,
      }
    },
    [],
  )
  const { form, set, dirty, markSaved, loaded } = useRecordForm(settings, toForm)
  // The dealership record the VAT rate was loaded from (its version is that PATCH's If-Match).
  const vat = useRecordForm(dealership, useCallback((d: DealershipRead): { vatRate: number | string } => ({ vatRate: d.vatRate ?? '' }), []))
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const dealershipPatch = useDealershipPatch(dealership.id)

  const settingsChanges = (): DealershipSettingsUpdate => {
    const c: DealershipSettingsUpdate = {}
    if (form.currency.trim().toUpperCase() !== loaded.currency) c.currency = form.currency.trim().toUpperCase()
    if (form.cashRounding !== String(loaded.cashRounding)) c.cashRounding = form.cashRounding
    if (Number(form.defaultPaymentTermsDays) !== loaded.defaultPaymentTermsDays) c.defaultPaymentTermsDays = Number(form.defaultPaymentTermsDays)
    const fiscal = joinFiscalYearStart(form.fiscalMonth, form.fiscalDay)
    if (fiscal !== loaded.fiscalYearStart) c.fiscalYearStart = fiscal
    if (orNull(form.accountingSystem) !== (loaded.accountingSystem ?? null)) c.accountingSystem = orNull(form.accountingSystem)
    if (orNull(form.mandant) !== (loaded.mandant ?? null)) c.mandant = orNull(form.mandant)
    if (form.chartOfAccountsVariant !== loaded.chartOfAccountsVariant) c.chartOfAccountsVariant = form.chartOfAccountsVariant
    if (form.periodLockPolicy !== loaded.periodLockPolicy) c.periodLockPolicy = form.periodLockPolicy
    return c
  }
  const vatRateChanged = String(vat.form.vatRate) !== String(vat.loaded.vatRate ?? '')

  const save = useMutation({
    mutationFn: async () => {
      const result: { settings: DealershipSettingsRead | null; dealership: DealershipRead | null } = { settings: null, dealership: null }
      const changes = settingsChanges()
      if (Object.keys(changes).length > 0) {
        result.settings = await api.patch<DealershipSettingsRead>(`/dealerships/${dealership.id}/settings`, changes, { 'If-Match': String(loaded.version) })
      }
      if (vatRateChanged) {
        result.dealership = await new Promise<DealershipRead>((resolve, reject) =>
          dealershipPatch.save(
            { changes: { vatRate: vat.form.vatRate === '' ? null : String(vat.form.vatRate) }, version: vat.loaded.version },
            { onSuccess: resolve, onError: reject },
          ),
        )
      }
      return result
    },
    onSuccess: (result) => {
      setError(null)
      setSaved(true)
      // The saved records become `loaded` before the cache delivers them, so
      // a second save in a row carries the right version.
      if (result.settings) {
        markSaved(result.settings)
        queryClient.setQueryData(settingsKey(dealership.id), result.settings)
      } else {
        markSaved(loaded)
      }
      if (result.dealership) vat.markSaved(result.dealership)
      else vat.markSaved(vat.loaded)
    },
    onError: (err) => {
      setSaved(false)
      setError(businessRefusalText(t, err))
    },
  })

  return (
    <SectionCard
      title={t('business.fiscal.title')}
      icon={<Calculator size={20} />}
      onSave={() => save.mutate()}
      dirty={(dirty || vat.dirty) && (Object.keys(settingsChanges()).length > 0 || vatRateChanged)}
      saving={save.isPending}
      error={error}
      onDismissError={() => setError(null)}
      saved={saved}
    >
      <Stack gap="md">
        <Text c="dimmed" fz="sm">
          {t('business.fiscal.hint')}
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label={t('business.fiscal.currency')} value={form.currency} onChange={(e) => set('currency', e.currentTarget.value.toUpperCase())} maxLength={3} description={t('business.fiscal.currencyHint')} />
          <Select label={t('business.fiscal.cashRounding')} data={CASH_ROUNDINGS.map((v) => ({ value: v, label: `${v} CHF` }))} value={form.cashRounding} onChange={(v) => v && set('cashRounding', v)} allowDeselect={false} />
          <NumberInput label={t('business.fiscal.paymentTerms')} value={form.defaultPaymentTermsDays} onChange={(v) => set('defaultPaymentTermsDays', v)} min={0} max={365} suffix={` ${t('business.fiscal.days')}`} allowDecimal={false} />
          <Select label={t('business.fiscal.fiscalMonth')} data={MONTHS.map((m) => ({ value: m, label: t(`business.fiscal.months.${m}`) }))} value={form.fiscalMonth} onChange={(v) => v && set('fiscalMonth', v)} allowDeselect={false} />
          <Select label={t('business.fiscal.fiscalDay')} data={DAY_OPTIONS} value={form.fiscalDay} onChange={(v) => v && set('fiscalDay', v)} allowDeselect={false} />
          <NumberInput label={t('business.fiscal.vatRate')} value={vat.form.vatRate} onChange={(v) => vat.set('vatRate', v)} min={0} max={100} decimalScale={2} suffix=" %" description={t('business.fiscal.vatRateHint')} />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label={t('business.fiscal.accountingSystem')} value={form.accountingSystem} onChange={(e) => set('accountingSystem', e.currentTarget.value)} placeholder="Abacus, bexio, Sage …" description={t('business.fiscal.accountingSystemHint')} />
          <TextInput label={t('business.fiscal.mandant')} value={form.mandant} onChange={(e) => set('mandant', e.currentTarget.value)} />
          <Select label={t('business.fiscal.chartOfAccounts')} data={CHART_VARIANTS.map((v) => ({ value: v, label: t(`business.fiscal.charts.${v}`) }))} value={form.chartOfAccountsVariant} onChange={(v) => v && set('chartOfAccountsVariant', v as ChartOfAccountsVariant)} allowDeselect={false} />
          <Select label={t('business.fiscal.periodLock')} data={PERIOD_LOCK_POLICIES.map((v) => ({ value: v, label: t(`business.fiscal.locks.${v}`) }))} value={form.periodLockPolicy} onChange={(v) => v && set('periodLockPolicy', v as PeriodLockPolicy)} allowDeselect={false} description={t('business.fiscal.periodLockHint')} />
        </SimpleGrid>
      </Stack>
    </SectionCard>
  )
}
