import { Loader, Stack, Text } from '@mantine/core'
import { History } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import { useAuth } from '../../auth/AuthContext'
import { formatCurrencyChf, formatDateOnly, formatDateTime, formatNumber } from '../../utils/format'
import { legalFormLabel } from '../../customerOptions'
import type { AuditEventRead, LegalForm } from '../../api/types'

type Translate = (key: string, options?: Record<string, unknown>) => string

// KAN-336 — every audit action the customer service writes (`record_audit_event`
// with entity_type "customer", app/customer/services/customer.py) has its own
// headline key; any other action reads as the generic `otherAction`, never as
// the raw verb.
const KNOWN_ACTIONS = new Set([
  'create',
  'update',
  'merge',
  // KAN-383: written on a customer merged earlier into one that is now merged itself.
  'merge_survivor_repointed',
  'phone_add',
  'phone_update',
  'phone_remove',
  'email_add',
  'email_update',
  'email_remove',
  'address_add',
  'address_update',
  'address_remove',
  'external_id_add',
  'external_id_update',
  'external_id_remove',
  'vehicle_party_add',
  'vehicle_party_update',
  'vehicle_party_remove',
  'credit_block_changed',
])

function actionLabel(action: string, t: Translate): string {
  return KNOWN_ACTIONS.has(action) ? t(`customerDetail.history.actions.${action}`) : t('customerDetail.history.otherAction')
}

// KAN-336 — customer-level events snapshot snake_case model attributes
// (address_street); contact-channel, vehicle-party and merge events write
// their own camelCase dicts (phoneE164). Both are looked up by the camelCase
// name. A field the Overview tab shows takes the Overview's label; the rest
// have their own key under customerDetail.history.fields.
const OVERVIEW_FIELD_LABEL: Record<string, string> = {
  salutation: 'customerDetail.overview.fields.salutation',
  firstName: 'customerDetail.overview.fields.firstName',
  lastName: 'customerDetail.overview.fields.lastName',
  title: 'customerDetail.overview.fields.title',
  birthDate: 'customerDetail.overview.fields.dateOfBirth',
  nationality: 'customerDetail.overview.fields.nationality',
  gender: 'customerDetail.overview.fields.gender',
  companyName: 'customerDetail.overview.fields.companyName',
  legalForm: 'customerDetail.overview.fields.legalForm',
  taxId: 'customerCreate.fields.uid',
  language: 'customerDetail.overview.fields.correspondenceLanguage',
  lifecycleStatus: 'customerDetail.overview.fields.lifecycleStatus',
  preferredChannel: 'customerDetail.overview.fields.preferredChannel',
  paymentTerms: 'customerDetail.overview.fields.paymentTerms',
  creditLimit: 'customerDetail.overview.fields.creditLimit',
  iban: 'customerDetail.overview.fields.iban',
  vatRegistered: 'customerDetail.overview.fields.vatRegistered',
  vatStatusConfirmed: 'customerDetail.overview.fields.vatStatusConfirmed',
  advisorId: 'customerDetail.overview.fields.advisor',
  customerSince: 'customerDetail.overview.fields.customerSince',
  nextFollowUp: 'customerDetail.overview.fields.nextFollowUp',
  website: 'customerDetail.overview.fields.website',
  newsletter: 'customerDetail.overview.fields.newsletter',
  tags: 'customerDetail.overview.fields.tags',
  notes: 'customerDetail.overview.fields.notes',
  customerNumber: 'customerDetail.overview.fields.customerNumber',
  addressStreet: 'customerDetail.overview.addressForm.street',
  addressLine2: 'customerDetail.overview.addressForm.line2',
  addressHouseNumber: 'customerDetail.overview.addressForm.houseNumber',
  addressPostalCode: 'customerDetail.overview.addressForm.postalCode',
  addressLocality: 'customerDetail.overview.addressForm.locality',
  addressCountry: 'customerDetail.overview.addressForm.country',
  addressCanton: 'customerDetail.overview.fields.canton',
  label: 'customerDetail.contactPoints.labelPlaceholder',
  isPrimary: 'customerDetail.contactPoints.primary',
  systemName: 'customerDetail.externalIds.systemLabel',
  externalId: 'customerDetail.externalIds.externalIdLabel',
  vehicleId: 'customerDetail.vehicles.columns.vehicle',
  role: 'customerDetail.vehicles.columns.role',
  effectiveFrom: 'customerDetail.vehicles.columns.since',
  effectiveTo: 'customerDetail.vehicles.columns.until',
}

const HISTORY_FIELDS = new Set([
  'dealershipId',
  'phoneType',
  'emailType',
  'addressType',
  'phoneE164',
  'emailAddress',
  'consentGranted',
  'consentScope',
  'consentSource',
  'consentTimestamp',
  'mergedIntoCustomerId',
  'duplicateOfCustomerId',
  'creditBlock',
  'creditBlockReason',
  'vehiclePartiesRepointed',
  'vehiclePartiesKeptClosed',
  'vehiclePartiesKeptClosedIds',
  'transactionsRepointed',
  'phonesRepointed',
  'phonesDropped',
  'emailsRepointed',
  'emailsDropped',
  'addressesRepointed',
  'addressesDropped',
  'externalIdsRepointed',
  'externalIdsDropped',
  'tagsRepointed',
  'tagsDropped',
])

// A field this tab does not know yet is shown under its key exactly as
// recorded — data, not an invented English label.
function fieldLabel(key: string, t: Translate): string {
  const k = camelKey(key)
  if (k in OVERVIEW_FIELD_LABEL) return t(OVERVIEW_FIELD_LABEL[k])
  if (HISTORY_FIELDS.has(k)) return t(`customerDetail.history.fields.${k}`)
  return key
}

// The stored enum values of the customer entity, translated through the same
// customerEnums.* keys the Overview tab's selects use.
const ENUM_KEY: Record<string, string> = {
  salutation: 'customerEnums.salutation',
  gender: 'customerEnums.gender',
  language: 'customerEnums.language',
  lifecycleStatus: 'customerEnums.lifecycleStatus',
  preferredChannel: 'customerEnums.preferredChannel',
  paymentTerms: 'customerEnums.paymentTerms',
  consentScope: 'customerEnums.consentScope',
  consentSource: 'customerEnums.consentSource',
  role: 'customerEnums.vehiclePartyRole',
  phoneType: 'customerEnums.contactType',
  emailType: 'customerEnums.contactType',
  addressType: 'customerEnums.addressType',
}

function enumLabel(key: string, value: string, t: Translate, exists: (key: string) => boolean): string | null {
  const k = camelKey(key)
  if (k === 'legalForm') return legalFormLabel(value as LegalForm)
  const base = ENUM_KEY[k]
  // A value with no key (one retired since it was recorded) is shown as stored.
  if (base === undefined || !exists(`${base}.${value}`)) return null
  return t(`${base}.${value}`)
}

// Every event on this tab is entity_type "customer" (app/customer/services/
// customer.py), so a value's meaning comes from the entity and key, not typeof
// (KAN-164): credit_limit reaches the audit log as a decimal *string*
// (`"12500.00"`), while customer_number, postal codes and IDs are figures that
// must never be grouped. Formatting is therefore opt-in per field of the
// customer entity; any other key or entity renders as recorded.
// KAN-180 — dates likewise: `_plain` writes a `date` as `1980-05-01` and a
// `datetime` as an offset-bearing ISO string, so the kind says which is a
// calendar day (`date`) and which an instant (`timestamp`).
type FieldKind = 'amountChf' | 'quantity' | 'date' | 'timestamp'

// A calendar day as the backend's `date.isoformat()` writes it.
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

function camelKey(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())
}

function fieldKind(entityType: string, key: string): FieldKind | null {
  if (entityType !== 'customer') return null
  const k = camelKey(key)
  if (k === 'creditLimit') return 'amountChf'
  // The merge event's counts (vehiclePartiesRepointed, vehiclePartiesKeptClosed, tagsDropped, …).
  if (/(Repointed|Dropped|KeptClosed)$/.test(k)) return 'quantity'
  // Customer-level `Date` columns (snake_case in the snapshot).
  if (k === 'birthDate' || k === 'customerSince' || k === 'nextFollowUp') return 'date'
  // Contact-channel consent and vehicle-party events write their own
  // camelCase dicts with `datetime` columns.
  if (k === 'consentTimestamp' || k === 'effectiveFrom' || k === 'effectiveTo') return 'timestamp'
  return null
}

interface ValueContext {
  t: Translate
  exists: (key: string) => boolean
  locale: string
  idLabels: Record<string, string>
  countryLabels: Record<string, string>
}

// Fields that hold an ISO country code from the `country` reference list.
const COUNTRY_KEYS = new Set(['nationality', 'addressCountry'])

function formatValue(
  entityType: string,
  key: string,
  v: unknown,
  { t, exists, locale, idLabels, countryLabels }: ValueContext,
): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no')
  if (typeof v === 'object') return JSON.stringify(v)
  if (entityType === 'customer' && typeof v === 'string') {
    // KAN-336 — an id renders the display label the customer record carries
    // for it (the advisor's name, a linked vehicle's make and model); one the
    // record carries no label for is shown as recorded.
    if (/Id$/.test(camelKey(key)) && idLabels[v] !== undefined) return idLabels[v]
    // A country code renders its name in the UI language, as on the Overview.
    if (COUNTRY_KEYS.has(camelKey(key)) && countryLabels[v] !== undefined) return countryLabels[v]
    const label = enumLabel(key, v, t, exists)
    if (label !== null) return label
  }
  const kind = fieldKind(entityType, key)
  // Anything that is not the date the kind promises (a redaction marker,
  // say) is shown as recorded, as the amount path does.
  if (kind === 'date') return typeof v === 'string' && DATE_ONLY.test(v) ? formatDateOnly(v, locale) : String(v)
  if (kind === 'timestamp') {
    return typeof v === 'string' && !Number.isNaN(new Date(v).getTime()) ? formatDateTime(v, locale) : String(v)
  }
  if (kind !== null && (typeof v === 'number' || typeof v === 'string') && v !== '' && Number.isFinite(Number(v))) {
    return kind === 'amountChf' ? formatCurrencyChf(Number(v)) : formatNumber(Number(v))
  }
  return String(v)
}

// before/after are raw entity snapshots (not a pre-computed diff), per
// AuditEventRead — only the keys that actually changed are worth showing,
// per "a human-readable before → after".
// KAN-180 — a key absent from one side (`undefined`) and null on the other
// is the same empty field, not a change: a create event's `before` is null,
// and a field that was never set must not read as `— → —`.
// KAN-348 — an empty collection is the same empty field: a create event's
// `after` carries `tags: []`, which must not read as `— → []`.
function emptyAsNull(value: unknown): unknown {
  if (value === undefined || (Array.isArray(value) && value.length === 0)) return null
  return value
}

function diff(before: Record<string, unknown> | null, after: Record<string, unknown> | null) {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])
  const changes: { key: string; before: unknown; after: unknown }[] = []
  for (const key of keys) {
    const b = emptyAsNull(before?.[key])
    const a = emptyAsNull(after?.[key])
    if (JSON.stringify(b) !== JSON.stringify(a)) changes.push({ key, before: b, after: a })
  }
  return changes
}

function HistoryEvent({
  event,
  isYou,
  locale,
  idLabels,
  countryLabels,
}: {
  event: AuditEventRead
  isYou: boolean
  locale: string
  idLabels: Record<string, string>
  countryLabels: Record<string, string>
}) {
  const { t, i18n } = useTranslation()
  const changes = diff(event.before, event.after)
  const ctx: ValueContext = { t, exists: (key) => i18n.exists(key), locale, idLabels, countryLabels }
  const actor = isYou ? t('customerDetail.history.you') : event.actorId ? t('customerDetail.history.user', { id: event.actorId.slice(0, 8) }) : t('customerDetail.history.system')
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: slate[4], marginTop: 6 }} />
        <div style={{ flex: 1, width: 1, backgroundColor: slate[2] }} />
      </div>
      <Stack gap={4} pb="md" style={{ flex: 1 }}>
        <Text size="sm">
          <Text component="span" fw={600}>
            {actor}
          </Text>
          {' · '}
          {actionLabel(event.action, t)}
        </Text>
        <Text size="xs" c="dimmed">
          {formatDateTime(event.createdAt, locale)}
        </Text>
        {changes.length > 0 && (
          <Stack gap={2} mt={4}>
            {changes.map((c) => (
              <Text key={c.key} size="xs" c="dimmed">
                <Text component="span" fw={500} c={slate[7]}>
                  {fieldLabel(c.key, t)}
                </Text>
                : {formatValue(event.entityType, c.key, c.before, ctx)} → {formatValue(event.entityType, c.key, c.after, ctx)}
              </Text>
            ))}
          </Stack>
        )}
      </Stack>
    </div>
  )
}

export function HistoryTab({
  events,
  loading,
  error,
  locale,
  idLabels = {},
  countryLabels = {},
}: {
  events: AuditEventRead[]
  loading: boolean
  error: string | null
  locale: string
  // KAN-336 — id → display label for the ids the customer record carries a
  // label for (advisor, linked vehicles); CustomerDetailPage builds it.
  idLabels?: Record<string, string>
  // ISO country code → its name in the UI language (the `country` reference
  // list, as the Overview renders nationality and address country).
  countryLabels?: Record<string, string>
}) {
  const { t } = useTranslation()
  const { user } = useAuth()

  if (loading) return <Loader size="sm" />
  if (error) {
    return (
      <Text size="sm" c="red">
        {error}
      </Text>
    )
  }
  if (events.length === 0) {
    return (
      <Stack align="center" gap="xs" py="xl">
        <History size={24} color={slate[4]} />
        <Text size="sm" fw={600}>
          {t('customerDetail.history.emptyState.title')}
        </Text>
        <Text size="sm" c="dimmed">
          {t('customerDetail.history.emptyState.description')}
        </Text>
      </Stack>
    )
  }

  // Newest first — audit-log doesn't guarantee order server-side.
  const sorted = [...events].sort((a, b) => b.createdAt.localeCompare(a.createdAt))

  return (
    <div>
      {sorted.map((event) => (
        <HistoryEvent key={event.id} event={event} isYou={event.actorId === user?.id} locale={locale} idLabels={idLabels} countryLabels={countryLabels} />
      ))}
    </div>
  )
}
