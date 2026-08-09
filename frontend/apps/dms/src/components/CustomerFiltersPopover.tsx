import { Select, TextInput } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { CANTON_OPTIONS, translatedCustomerTypeOptions, translatedLanguageOptions, translatedLifecycleOptions } from '../customerOptions'
import type { CustomerLifecycleStatus, CustomerType, Language } from '../api/types'

export interface CustomerFilters {
  customerType: CustomerType | null
  lifecycleStatus: CustomerLifecycleStatus | null
  language: Language | null
  canton: string | null
  changedSince: string | null
}

export const EMPTY_CUSTOMER_FILTERS: CustomerFilters = {
  customerType: null,
  lifecycleStatus: null,
  language: null,
  canton: null,
  changedSince: null,
}

export function countActiveFilters(filters: CustomerFilters): number {
  return Object.values(filters).filter((v) => v !== null && v !== '').length
}

/**
 * FR-02: "Filterable by lifecycle status, language, canton, customer type
 * and 'changed since'." The actual field set rendered inside the generic
 * ui-kit FilterButton's popover — app-specific, unlike the shell around it.
 */
export function CustomerFiltersPopover({
  filters,
  onChange,
}: {
  filters: CustomerFilters
  onChange: (filters: CustomerFilters) => void
}) {
  const { t } = useTranslation()
  const set = <K extends keyof CustomerFilters>(key: K, value: CustomerFilters[K]) =>
    onChange({ ...filters, [key]: value })

  return (
    <>
      <Select
        label={t('customersList.filters.customerType')}
        data={translatedCustomerTypeOptions(t)}
        clearable
        value={filters.customerType}
        onChange={(value) => set('customerType', value as CustomerType | null)}
      />
      <Select
        label={t('customersList.filters.lifecycleStatus')}
        data={translatedLifecycleOptions(t)}
        clearable
        value={filters.lifecycleStatus}
        onChange={(value) => set('lifecycleStatus', value as CustomerLifecycleStatus | null)}
      />
      <Select
        label={t('customersList.filters.language')}
        data={translatedLanguageOptions(t)}
        clearable
        value={filters.language}
        onChange={(value) => set('language', value as Language | null)}
      />
      <Select
        label={t('customersList.filters.canton')}
        data={CANTON_OPTIONS}
        clearable
        searchable
        value={filters.canton}
        onChange={(value) => set('canton', value)}
      />
      <TextInput
        label={t('customersList.filters.changedSince')}
        type="date"
        value={filters.changedSince ?? ''}
        onChange={(e) => set('changedSince', e.currentTarget.value || null)}
      />
    </>
  )
}
