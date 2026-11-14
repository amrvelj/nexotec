import type { DateCondition, FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type {
  ConsentScope,
  ConsentSource,
  CustomerLifecycleStatus,
  CustomerSource,
  CustomerType,
  EmailType,
  Gender,
  LegalForm,
  PaymentTerms,
  PhoneType,
  PreferredChannel,
  Salutation,
} from './api/types'

// The correspondence language is named in itself for every user (KAN-343):
// one list, shared with the reference-data screen.
export { LANGUAGE_OPTIONS } from './i18n/languageEndonyms'

export const CUSTOMER_TYPE_OPTIONS: { value: CustomerType; label: string }[] = [
  { value: 'individual', label: 'Individual' },
  { value: 'business', label: 'Business' },
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

// KAN-104 item 4: the six legal-form codes, labelled in the UI language
// (customerEnums.legalForm.*). SA, Sàrl and Sagl are the French/Italian
// names of AG and GmbH, not separate forms (.claude/rules/customer.md).
const LEGAL_FORM_VALUES: LegalForm[] = ['ag', 'gmbh', 'einzelfirma', 'verein', 'genossenschaft', 'weitere']

export function translatedLegalFormOptions(t: Translate): { value: LegalForm; label: string }[] {
  return LEGAL_FORM_VALUES.map((value) => ({ value, label: t(`customerEnums.legalForm.${value}`) }))
}

export function translatedLegalFormLabel(t: Translate, value: LegalForm): string {
  return t(`customerEnums.legalForm.${value}`)
}

// Single-value label helpers, same shape as translatedLifecycleLabel /
// translatedCustomerTypeLabel above — for a grid cell that renders one
// stored enum value rather than a full option list (KAN-51).
export function translatedSalutationLabel(t: Translate, value: Salutation): string {
  return t(`customerEnums.salutation.${value}`)
}

export function translatedPreferredChannelLabel(t: Translate, value: PreferredChannel): string {
  return t(`customerEnums.preferredChannel.${value}`)
}

export function translatedSourceLabel(t: Translate, value: CustomerSource): string {
  return t(`customerEnums.source.${value}`)
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

/** KAN-200 (FR-A-37): the advisor filter's "unassigned" option — the queue
 * a leaver's hand-over to nobody fills. */
export const UNASSIGNED_ADVISOR = '__unassigned__'

/** A date field's relative conditions go to their own parameters: the
 * field's `param` is the absolute lower bound for incremental sync. The
 * server resolves each against its own clock, in the session time zone
 * (U-09; KAN-458, KAN-474) — never the browser, whose «today» as a bare
 * date was bound as UTC midnight. */
const RELATIVE_DATE_PARAMS: Record<
  string,
  { inTheLastDays: string; moreThanDaysAgo: string; sinceStartOf: string }
> = {
  updated_since: {
    inTheLastDays: 'updated_in_last_days',
    moreThanDaysAgo: 'updated_more_than_days_ago',
    sinceStartOf: 'updated_since_start_of',
  },
}

/** `updated_since_start_of`'s values: the current day, week, month or year. */
const CALENDAR_PERIOD: Partial<Record<DateCondition, 'day' | 'week' | 'month' | 'year'>> = {
  today: 'day',
  thisWeek: 'week',
  thisMonth: 'month',
  thisYear: 'year',
}

/**
 * FilterPredicate[] → `/customers` query params. `/customers` accepts a
 * handful of fixed, independent parameters — no generic predicate engine
 * exists server-side — and each field's `param` comes from the same column
 * defs the filter builder is derived from (`meta.filter`, § ADR-058), so
 * there is no second hand-maintained map to drift.
 *
 * Every field and condition the columns offer is sent;
 * `architecture/grid-filters-are-applied.test.ts` fails if one is not
 * (KAN-458). The page passes only offered predicates (`offeredPredicates`).
 */
export function applyCustomerFilters(params: URLSearchParams, predicates: FilterPredicate[], fields: FilterFieldDef[]): void {
  const fieldById = new Map(fields.map((f) => [f.id, f]))
  for (const predicate of predicates) {
    const field = fieldById.get(predicate.fieldId)
    const paramName = field ? (field.param ?? field.id) : undefined
    if (paramName === undefined) {
      // Not a field this grid offers — `offeredPredicates` never passes one.
    } else if (predicate.type === 'date') {
      const relative = RELATIVE_DATE_PARAMS[paramName]
      const condition = predicate.condition as DateCondition
      const period = CALENDAR_PERIOD[condition]
      // A missing day count reads as 0, as the kit's `resolveRelativeDateRange` does.
      if (!relative) {
        // A date field without relative parameters — none on this grid today.
      } else if (condition === 'inTheLastDays') params.set(relative.inTheLastDays, String(predicate.days ?? 0))
      else if (condition === 'moreThanDaysAgo') params.set(relative.moreThanDaysAgo, String(predicate.days ?? 0))
      else if (period) params.set(relative.sinceStartOf, period)
    } else if (predicate.condition === 'is' && typeof predicate.value === 'string') {
      if (paramName === 'advisorUserId' && predicate.value === UNASSIGNED_ADVISOR) params.set('unassigned', 'true')
      else params.set(paramName, predicate.value)
    } else if (predicate.condition === 'isNot' && typeof predicate.value === 'string') {
      // KAN-476: the endpoint's exclusion — `advisorUserIdNot` beside the
      // camelCase `advisorUserId`, `…_not` beside the snake_case ones; «ist
      // nicht Nicht zugewiesen» is the customers that have an advisor.
      if (paramName === 'advisorUserId') {
        if (predicate.value === UNASSIGNED_ADVISOR) params.set('assigned', 'true')
        else params.set('advisorUserIdNot', predicate.value)
      } else if (paramName === 'contactGroup') {
        // KAN-466: camelCase like `advisorUserId`.
        params.set('contactGroupNot', predicate.value)
      } else params.set(`${paramName}_not`, predicate.value)
    }
  }
}
