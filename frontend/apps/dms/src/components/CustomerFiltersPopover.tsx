import { Select, TextInput } from '@mantine/core'
import { CANTON_OPTIONS, CUSTOMER_TYPE_OPTIONS, LANGUAGE_OPTIONS, LIFECYCLE_OPTIONS } from '../customerOptions'
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
  const set = <K extends keyof CustomerFilters>(key: K, value: CustomerFilters[K]) =>
    onChange({ ...filters, [key]: value })

  return (
    <>
      <Select
        label="Customer type"
        data={CUSTOMER_TYPE_OPTIONS}
        clearable
        value={filters.customerType}
        onChange={(value) => set('customerType', value as CustomerType | null)}
      />
      <Select
        label="Lifecycle status"
        data={LIFECYCLE_OPTIONS}
        clearable
        value={filters.lifecycleStatus}
        onChange={(value) => set('lifecycleStatus', value as CustomerLifecycleStatus | null)}
      />
      <Select
        label="Language"
        data={LANGUAGE_OPTIONS}
        clearable
        value={filters.language}
        onChange={(value) => set('language', value as Language | null)}
      />
      <Select
        label="Canton"
        data={CANTON_OPTIONS}
        clearable
        searchable
        value={filters.canton}
        onChange={(value) => set('canton', value)}
      />
      <TextInput
        label="Changed since"
        type="date"
        value={filters.changedSince ?? ''}
        onChange={(e) => set('changedSince', e.currentTarget.value || null)}
      />
    </>
  )
}
