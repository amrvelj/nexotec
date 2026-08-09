import type {
  CustomerLifecycleStatus,
  CustomerSource,
  CustomerType,
  EmailType,
  Language,
  LegalForm,
  PhoneType,
  PreferredChannel,
  Salutation,
} from './api/types'

export const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'de', label: 'Deutsch' },
  { value: 'fr', label: 'Français' },
  { value: 'it', label: 'Italiano' },
  { value: 'en', label: 'English' },
]

export const CUSTOMER_TYPE_OPTIONS: { value: CustomerType; label: string }[] = [
  { value: 'individual', label: 'Individual' },
  { value: 'business', label: 'Business' },
]

// The 26 Swiss cantons — matches `derive_canton`'s output alphabet
// (app/core/postal_codes.py), not a general-purpose country/region list.
export const CANTON_OPTIONS: { value: string; label: string }[] = [
  { value: 'AG', label: 'Aargau' },
  { value: 'AI', label: 'Appenzell Innerrhoden' },
  { value: 'AR', label: 'Appenzell Ausserrhoden' },
  { value: 'BE', label: 'Bern' },
  { value: 'BL', label: 'Basel-Landschaft' },
  { value: 'BS', label: 'Basel-Stadt' },
  { value: 'FR', label: 'Fribourg' },
  { value: 'GE', label: 'Genève' },
  { value: 'GL', label: 'Glarus' },
  { value: 'GR', label: 'Graubünden' },
  { value: 'JU', label: 'Jura' },
  { value: 'LU', label: 'Luzern' },
  { value: 'NE', label: 'Neuchâtel' },
  { value: 'NW', label: 'Nidwalden' },
  { value: 'OW', label: 'Obwalden' },
  { value: 'SG', label: 'St. Gallen' },
  { value: 'SH', label: 'Schaffhausen' },
  { value: 'SO', label: 'Solothurn' },
  { value: 'SZ', label: 'Schwyz' },
  { value: 'TG', label: 'Thurgau' },
  { value: 'TI', label: 'Ticino' },
  { value: 'UR', label: 'Uri' },
  { value: 'VD', label: 'Vaud' },
  { value: 'VS', label: 'Valais' },
  { value: 'ZG', label: 'Zug' },
  { value: 'ZH', label: 'Zürich' },
]

// 'merged' is excluded — neither CustomerCreate nor CustomerUpdate accepts
// it; a customer can only reach that status via POST /customers/{id}/merge.
export const LIFECYCLE_OPTIONS: { value: CustomerLifecycleStatus; label: string }[] = [
  { value: 'prospect', label: 'Prospect' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'do_not_contact', label: 'Do not contact' },
]

export const SOURCE_OPTIONS: { value: CustomerSource; label: string }[] = [
  { value: 'walk_in', label: 'Walk-in' },
  { value: 'phone', label: 'Phone' },
  { value: 'web_lead', label: 'Web lead' },
  { value: 'marketplace', label: 'Marketplace' },
  { value: 'other', label: 'Other' },
]

export const SALUTATION_OPTIONS: { value: Salutation; label: string }[] = [
  { value: 'herr', label: 'Herr' },
  { value: 'frau', label: 'Frau' },
  { value: 'firma', label: 'Firma' },
  { value: 'neutral', label: 'Neutral' },
]

export const LEGAL_FORM_OPTIONS: { value: LegalForm; label: string }[] = [
  { value: 'ag', label: 'AG' },
  { value: 'gmbh', label: 'GmbH' },
  { value: 'einzelfirma', label: 'Einzelfirma' },
  { value: 'verein', label: 'Verein' },
  { value: 'genossenschaft', label: 'Genossenschaft' },
  { value: 'weitere', label: 'Weitere' },
]

export const PREFERRED_CHANNEL_OPTIONS: { value: PreferredChannel; label: string }[] = [
  { value: 'mail', label: 'Mail' },
  { value: 'call', label: 'Call' },
  { value: 'message', label: 'Message' },
  { value: 'letter', label: 'Letter' },
]

export const PHONE_TYPE_OPTIONS: { value: PhoneType; label: string }[] = [
  { value: 'mobile', label: 'Mobile' },
  { value: 'private', label: 'Private' },
  { value: 'office', label: 'Office' },
]

export const EMAIL_TYPE_OPTIONS: { value: EmailType; label: string }[] = [
  { value: 'private', label: 'Private' },
  { value: 'business', label: 'Business' },
]

// FR-13: translated variants of the option lists above, for screens that
// have adopted i18n (the Customers List page so far — see customerEnums.*
// in the locale bundles). The plain English constants above stay as-is
// for screens not yet migrated, so this is additive, not a replacement.
type Translate = (key: string) => string

export function translatedCustomerTypeOptions(t: Translate): { value: CustomerType; label: string }[] {
  return [
    { value: 'individual', label: t('customerEnums.customerType.individual') },
    { value: 'business', label: t('customerEnums.customerType.business') },
  ]
}

export function translatedLifecycleOptions(t: Translate): { value: CustomerLifecycleStatus; label: string }[] {
  return [
    { value: 'prospect', label: t('customerEnums.lifecycleStatus.prospect') },
    { value: 'active', label: t('customerEnums.lifecycleStatus.active') },
    { value: 'inactive', label: t('customerEnums.lifecycleStatus.inactive') },
    { value: 'do_not_contact', label: t('customerEnums.lifecycleStatus.do_not_contact') },
  ]
}

export function translatedLanguageOptions(t: Translate): { value: Language; label: string }[] {
  return [
    { value: 'de', label: t('customerEnums.language.de') },
    { value: 'fr', label: t('customerEnums.language.fr') },
    { value: 'it', label: t('customerEnums.language.it') },
    { value: 'en', label: t('customerEnums.language.en') },
  ]
}

export function translatedLifecycleLabel(t: Translate, status: CustomerLifecycleStatus): string {
  return t(`customerEnums.lifecycleStatus.${status}`)
}

export function translatedCustomerTypeLabel(t: Translate, type: CustomerType): string {
  return t(`customerEnums.customerType.${type}`)
}
