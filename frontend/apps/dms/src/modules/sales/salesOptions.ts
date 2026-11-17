import type { SalesDealStatus } from '@nexotec/ui-kit'

type Translate = (key: string) => string

export function translatedSalesEntityTypeLabel(t: Translate, entityType: 'offer' | 'contract'): string {
  return t(`salesEnums.entityType.${entityType}`)
}

export function translatedSalesEntityTypeOptions(t: Translate): { value: 'offer' | 'contract'; label: string }[] {
  return [
    { value: 'offer', label: t('salesEnums.entityType.offer') },
    { value: 'contract', label: t('salesEnums.entityType.contract') },
  ]
}

// The grid's ONE shared status column spans both vocabularies (see
// SalesStatusBadge's own docstring) — the i18n key is looked up by the raw
// status string sales_deal actually stores, disambiguated where the two
// vocabularies collide ("open" vs "pending", both German "Offen") only by
// the badge's own fixed tone/label map, never by a second lookup here.
export function translatedSalesDealStatusLabel(t: Translate, status: SalesDealStatus): string {
  return t(`salesEnums.dealStatus.${status}`)
}
