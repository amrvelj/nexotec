import type {
  ConsentScope,
  ConsentSource,
  CustomerLifecycleStatus,
  CustomerSource,
  CustomerType,
  EmailType,
  Gender,
  Language,
  LegalForm,
  PaymentTerms,
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

// D-21 (ruled 2026-09-07) — email / phone / post / whatsapp. `message`
// (legacy SMS, no clean target) is retained on the backend enum so old
// rows load, but is never offered as a choice.
export const PREFERRED_CHANNEL_OPTIONS: { value: PreferredChannel; label: string }[] = [
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'post', label: 'Post' },
  { value: 'whatsapp', label: 'WhatsApp' },
]

export const PHONE_TYPE_OPTIONS: { value: PhoneType; label: string }[] = [
  { value: 'mobile', label: 'Mobile' },
  { value: 'landline', label: 'Landline' },
  { value: 'work', label: 'Work' },
  { value: 'fax', label: 'Fax' },
]

export const EMAIL_TYPE_OPTIONS: { value: EmailType; label: string }[] = [
  { value: 'personal', label: 'Personal' },
  { value: 'work', label: 'Work' },
  { value: 'invoicing', label: 'Invoicing' },
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

export function translatedSalutationOptions(t: Translate): { value: Salutation; label: string }[] {
  return [
    { value: 'herr', label: t('customerEnums.salutation.herr') },
    { value: 'frau', label: t('customerEnums.salutation.frau') },
    { value: 'firma', label: t('customerEnums.salutation.firma') },
    { value: 'neutral', label: t('customerEnums.salutation.neutral') },
  ]
}

// D-21 — the pickable set is the four current values. `message` (legacy
// SMS) is never offered; translatedPreferredChannelLabel still resolves it
// for a customer who already holds it.
export function translatedPreferredChannelOptions(t: Translate): { value: PreferredChannel; label: string }[] {
  return [
    { value: 'email', label: t('customerEnums.preferredChannel.email') },
    { value: 'phone', label: t('customerEnums.preferredChannel.phone') },
    { value: 'post', label: t('customerEnums.preferredChannel.post') },
    { value: 'whatsapp', label: t('customerEnums.preferredChannel.whatsapp') },
  ]
}

export function translatedSourceOptions(t: Translate): { value: CustomerSource; label: string }[] {
  return [
    { value: 'walk_in', label: t('customerEnums.source.walk_in') },
    { value: 'phone', label: t('customerEnums.source.phone') },
    { value: 'web_lead', label: t('customerEnums.source.web_lead') },
    { value: 'marketplace', label: t('customerEnums.source.marketplace') },
    { value: 'other', label: t('customerEnums.source.other') },
  ]
}

// Single-value label helpers, same shape as translatedLifecycleLabel /
// translatedCustomerTypeLabel above — for a grid cell that renders one
// stored enum value rather than a full option list (KAN-51). legalForm has
// no locale bundle (AG / GmbH / Einzelfirma read the same in all four
// languages — the customer detail screen renders LEGAL_FORM_OPTIONS
// directly for the same reason), so it uses that constant, not `t`.
export function translatedSalutationLabel(t: Translate, value: Salutation): string {
  return t(`customerEnums.salutation.${value}`)
}

export function translatedPreferredChannelLabel(t: Translate, value: PreferredChannel): string {
  return t(`customerEnums.preferredChannel.${value}`)
}

export function translatedSourceLabel(t: Translate, value: CustomerSource): string {
  return t(`customerEnums.source.${value}`)
}

export function legalFormLabel(value: LegalForm): string {
  return LEGAL_FORM_OPTIONS.find((o) => o.value === value)?.label ?? value
}

// FR-17 / FR-18 stored fields (KAN-50). `gender` is segmentation-only and
// distinct from `salutation`; `paymentTerms` is FR-18 region 4.
const GENDER_VALUES: Gender[] = ['female', 'male', 'other', 'unspecified']
const PAYMENT_TERMS_VALUES: PaymentTerms[] = ['prepayment', 'net_10', 'net_30', 'net_60', 'on_delivery']

export function translatedGenderOptions(t: Translate): { value: Gender; label: string }[] {
  return GENDER_VALUES.map((value) => ({ value, label: t(`customerEnums.gender.${value}`) }))
}

export function translatedGenderLabel(t: Translate, value: Gender): string {
  return t(`customerEnums.gender.${value}`)
}

export function translatedPaymentTermsOptions(t: Translate): { value: PaymentTerms; label: string }[] {
  return PAYMENT_TERMS_VALUES.map((value) => ({ value, label: t(`customerEnums.paymentTerms.${value}`) }))
}

export function translatedPaymentTermsLabel(t: Translate, value: PaymentTerms): string {
  return t(`customerEnums.paymentTerms.${value}`)
}

// FR-23 §1 (KAN-52) — per-channel consent scope + source, closed enums,
// stored language-independent.
const CONSENT_SCOPE_VALUES: ConsentScope[] = ['marketing', 'invoicing', 'service']
const CONSENT_SOURCE_VALUES: ConsentSource[] = ['form', 'counter', 'web', 'phone']

export function translatedConsentScopeOptions(t: Translate): { value: ConsentScope; label: string }[] {
  return CONSENT_SCOPE_VALUES.map((value) => ({ value, label: t(`customerEnums.consentScope.${value}`) }))
}

export function translatedConsentScopeLabel(t: Translate, value: ConsentScope): string {
  return t(`customerEnums.consentScope.${value}`)
}

export function translatedConsentSourceOptions(t: Translate): { value: ConsentSource; label: string }[] {
  return CONSENT_SOURCE_VALUES.map((value) => ({ value, label: t(`customerEnums.consentSource.${value}`) }))
}

export function translatedConsentSourceLabel(t: Translate, value: ConsentSource): string {
  return t(`customerEnums.consentSource.${value}`)
}

export function translatedVehiclePartyRoleLabel(t: Translate, role: 'owner' | 'keeper' | 'driver'): string {
  return t(`customerEnums.vehiclePartyRole.${role}`)
}

export function translatedPhoneTypeOptions(t: Translate): { value: PhoneType; label: string }[] {
  return [
    { value: 'mobile', label: t('customerEnums.contactType.mobile') },
    { value: 'landline', label: t('customerEnums.contactType.landline') },
    { value: 'work', label: t('customerEnums.contactType.work') },
    { value: 'fax', label: t('customerEnums.contactType.fax') },
  ]
}

export function translatedEmailTypeOptions(t: Translate): { value: EmailType; label: string }[] {
  return [
    { value: 'personal', label: t('customerEnums.contactType.personal') },
    { value: 'work', label: t('customerEnums.contactType.work') },
    { value: 'invoicing', label: t('customerEnums.contactType.invoicing') },
  ]
}
