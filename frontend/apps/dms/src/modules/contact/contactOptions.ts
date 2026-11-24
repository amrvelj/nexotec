import type { DateCondition, FilterFieldDef, FilterPredicate } from '@nexotec/ui-kit'
import type {
  ConsentScope,
  ContactRole,
  ContactRead,
  ConsentSource,
  ContactLifecycleStatus,
  ContactSource,
  ContactType,
  EmailType,
  Gender,
  LegalForm,
  PaymentTerms,
  PhoneType,
  PreferredChannel,
  Salutation,
} from '../../api/types'

// The correspondence language is named in itself for every user (KAN-343):
// one list, shared with the reference-data screen.
export { LANGUAGE_OPTIONS } from '../../i18n/languageEndonyms'

export const CONTACT_TYPE_OPTIONS: { value: ContactType; label: string }[] = [
  { value: 'individual', label: 'Individual' },
  { value: 'business', label: 'Business' },
]

// 'merged' is excluded — neither ContactCreate nor ContactUpdate accepts
// it; a contact can only reach that status via POST /contacts/{id}/merge.
export const LIFECYCLE_OPTIONS: { value: ContactLifecycleStatus; label: string }[] = [
  { value: 'prospect', label: 'Prospect' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'do_not_contact', label: 'Do not contact' },
]

export const SOURCE_OPTIONS: { value: ContactSource; label: string }[] = [
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
// have adopted i18n (the Contacts List page so far — see contactEnums.*
// in the locale bundles). The plain English constants above stay as-is
// for screens not yet migrated, so this is additive, not a replacement.
type Translate = (key: string) => string

export function translatedContactTypeOptions(t: Translate): { value: ContactType; label: string }[] {
  return [
    { value: 'individual', label: t('contactEnums.contactType.individual') },
    { value: 'business', label: t('contactEnums.contactType.business') },
  ]
}

export function translatedLifecycleOptions(t: Translate): { value: ContactLifecycleStatus; label: string }[] {
  return [
    { value: 'prospect', label: t('contactEnums.lifecycleStatus.prospect') },
    { value: 'active', label: t('contactEnums.lifecycleStatus.active') },
    { value: 'inactive', label: t('contactEnums.lifecycleStatus.inactive') },
    { value: 'do_not_contact', label: t('contactEnums.lifecycleStatus.do_not_contact') },
  ]
}

// KAN-464: the roles a contact holds (Kunde, Lieferant). Status, advisor and
// credit block belong to the customer role (Q1, app/contact/services/roles.py).
export const CONTACT_ROLES: ContactRole[] = ['customer', 'supplier']

export function translatedRoleOptions(t: Translate): { value: ContactRole; label: string }[] {
  return CONTACT_ROLES.map((value) => ({ value, label: t(`contactEnums.role.${value}`) }))
}

export function translatedRoleLabel(t: Translate, role: ContactRole): string {
  return t(`contactEnums.role.${role}`)
}

/** Whether the contact holds the customer role — the one its status, advisor
 * and credit block belong to. */
export function holdsCustomerRole(contact: Pick<ContactRead, 'roles'>): boolean {
  return (contact.roles ?? []).includes('customer')
}

/** Whether the contact's lifecycle status means anything to show: a contact
 * without the customer role has none, unless it is do-not-contact or merged
 * (those belong to every contact). */
export function showsLifecycleStatus(contact: Pick<ContactRead, 'roles' | 'lifecycleStatus'>): boolean {
  return holdsCustomerRole(contact) || contact.lifecycleStatus === 'do_not_contact' || contact.lifecycleStatus === 'merged'
}

export function translatedLifecycleLabel(t: Translate, status: ContactLifecycleStatus): string {
  return t(`contactEnums.lifecycleStatus.${status}`)
}

export function translatedContactTypeLabel(t: Translate, type: ContactType): string {
  return t(`contactEnums.contactType.${type}`)
}

export function translatedSalutationOptions(t: Translate): { value: Salutation; label: string }[] {
  return [
    { value: 'herr', label: t('contactEnums.salutation.herr') },
    { value: 'frau', label: t('contactEnums.salutation.frau') },
    { value: 'firma', label: t('contactEnums.salutation.firma') },
    { value: 'neutral', label: t('contactEnums.salutation.neutral') },
  ]
}

// D-21 — the pickable set is the four current values. `message` (legacy
// SMS) is never offered; translatedPreferredChannelLabel still resolves it
// for a contact who already holds it.
export function translatedPreferredChannelOptions(t: Translate): { value: PreferredChannel; label: string }[] {
  return [
    { value: 'email', label: t('contactEnums.preferredChannel.email') },
    { value: 'phone', label: t('contactEnums.preferredChannel.phone') },
    { value: 'post', label: t('contactEnums.preferredChannel.post') },
    { value: 'whatsapp', label: t('contactEnums.preferredChannel.whatsapp') },
  ]
}

export function translatedSourceOptions(t: Translate): { value: ContactSource; label: string }[] {
  return [
    { value: 'walk_in', label: t('contactEnums.source.walk_in') },
    { value: 'phone', label: t('contactEnums.source.phone') },
    { value: 'web_lead', label: t('contactEnums.source.web_lead') },
    { value: 'marketplace', label: t('contactEnums.source.marketplace') },
    { value: 'other', label: t('contactEnums.source.other') },
  ]
}

// KAN-104 item 4: the six legal-form codes, labelled in the UI language
// (contactEnums.legalForm.*). SA, Sàrl and Sagl are the French/Italian
// names of AG and GmbH, not separate forms (.claude/rules/contact.md).
const LEGAL_FORM_VALUES: LegalForm[] = ['ag', 'gmbh', 'einzelfirma', 'verein', 'genossenschaft', 'weitere']

export function translatedLegalFormOptions(t: Translate): { value: LegalForm; label: string }[] {
  return LEGAL_FORM_VALUES.map((value) => ({ value, label: t(`contactEnums.legalForm.${value}`) }))
}

export function translatedLegalFormLabel(t: Translate, value: LegalForm): string {
  return t(`contactEnums.legalForm.${value}`)
}

// Single-value label helpers, same shape as translatedLifecycleLabel /
// translatedContactTypeLabel above — for a grid cell that renders one
// stored enum value rather than a full option list (KAN-51).
export function translatedSalutationLabel(t: Translate, value: Salutation): string {
  return t(`contactEnums.salutation.${value}`)
}

export function translatedPreferredChannelLabel(t: Translate, value: PreferredChannel): string {
  return t(`contactEnums.preferredChannel.${value}`)
}

export function translatedSourceLabel(t: Translate, value: ContactSource): string {
  return t(`contactEnums.source.${value}`)
}

// FR-17 / FR-18 stored fields (KAN-50). `gender` is segmentation-only and
// distinct from `salutation`; `paymentTerms` is FR-18 region 4.
const GENDER_VALUES: Gender[] = ['female', 'male', 'other', 'unspecified']
const PAYMENT_TERMS_VALUES: PaymentTerms[] = ['prepayment', 'net_10', 'net_30', 'net_60', 'on_delivery']

export function translatedGenderOptions(t: Translate): { value: Gender; label: string }[] {
  return GENDER_VALUES.map((value) => ({ value, label: t(`contactEnums.gender.${value}`) }))
}

export function translatedGenderLabel(t: Translate, value: Gender): string {
  return t(`contactEnums.gender.${value}`)
}

export function translatedPaymentTermsOptions(t: Translate): { value: PaymentTerms; label: string }[] {
  return PAYMENT_TERMS_VALUES.map((value) => ({ value, label: t(`contactEnums.paymentTerms.${value}`) }))
}

export function translatedPaymentTermsLabel(t: Translate, value: PaymentTerms): string {
  return t(`contactEnums.paymentTerms.${value}`)
}

// FR-23 §1 (KAN-52) — per-channel consent scope + source, closed enums,
// stored language-independent.
const CONSENT_SCOPE_VALUES: ConsentScope[] = ['marketing', 'invoicing', 'service']
const CONSENT_SOURCE_VALUES: ConsentSource[] = ['form', 'counter', 'web', 'phone']

export function translatedConsentScopeOptions(t: Translate): { value: ConsentScope; label: string }[] {
  return CONSENT_SCOPE_VALUES.map((value) => ({ value, label: t(`contactEnums.consentScope.${value}`) }))
}

export function translatedConsentScopeLabel(t: Translate, value: ConsentScope): string {
  return t(`contactEnums.consentScope.${value}`)
}

export function translatedConsentSourceOptions(t: Translate): { value: ConsentSource; label: string }[] {
  return CONSENT_SOURCE_VALUES.map((value) => ({ value, label: t(`contactEnums.consentSource.${value}`) }))
}

export function translatedConsentSourceLabel(t: Translate, value: ConsentSource): string {
  return t(`contactEnums.consentSource.${value}`)
}

export function translatedVehiclePartyRoleLabel(t: Translate, role: 'owner' | 'keeper' | 'driver'): string {
  return t(`contactEnums.vehiclePartyRole.${role}`)
}

export function translatedPhoneTypeOptions(t: Translate): { value: PhoneType; label: string }[] {
  return [
    { value: 'mobile', label: t('contactEnums.channelType.mobile') },
    { value: 'landline', label: t('contactEnums.channelType.landline') },
    { value: 'work', label: t('contactEnums.channelType.work') },
    { value: 'fax', label: t('contactEnums.channelType.fax') },
  ]
}

export function translatedEmailTypeOptions(t: Translate): { value: EmailType; label: string }[] {
  return [
    { value: 'personal', label: t('contactEnums.channelType.personal') },
    { value: 'work', label: t('contactEnums.channelType.work') },
    { value: 'invoicing', label: t('contactEnums.channelType.invoicing') },
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
 * FilterPredicate[] → `/contacts` query params. `/contacts` accepts a
 * handful of fixed, independent parameters — no generic predicate engine
 * exists server-side — and each field's `param` comes from the same column
 * defs the filter builder is derived from (`meta.filter`, § ADR-058), so
 * there is no second hand-maintained map to drift.
 *
 * Every field and condition the columns offer is sent;
 * `architecture/grid-filters-are-applied.test.ts` fails if one is not
 * (KAN-458). The page passes only offered predicates (`offeredPredicates`).
 */
export function applyContactFilters(params: URLSearchParams, predicates: FilterPredicate[], fields: FilterFieldDef[]): void {
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
      // nicht Nicht zugewiesen» is the contacts that have an advisor.
      if (paramName === 'advisorUserId') {
        if (predicate.value === UNASSIGNED_ADVISOR) params.set('assigned', 'true')
        else params.set('advisorUserIdNot', predicate.value)
      } else if (paramName === 'contactGroup' || paramName === 'role' || paramName === 'contactType') {
        // KAN-466, KAN-464: camelCase like `advisorUserId` (`contactType` since
        // KAN-464 PR-3 renamed it; the other legacy names keep `…_not`).
        params.set(`${paramName}Not`, predicate.value)
      } else params.set(`${paramName}_not`, predicate.value)
    }
  }
}
