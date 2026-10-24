import type { TFunction } from 'i18next'
import { ApiError } from './api/client'
import type { ChartOfAccountsVariant, FranchiseType, PeriodLockPolicy, QrReferenceType } from './api/types'

/**
 * KAN-203 — Dealer Administration › Business: the option lists and the
 * refusal texts the five sections share. An API refusal is shown in the UI
 * language (KAN-398): `details.reason` and the status map to a key in all
 * four bundles, anything else to the generic message — never `err.message`.
 */

export const FRANCHISE_TYPES: FranchiseType[] = ['franchise', 'independent']
export const QR_REFERENCE_TYPES: QrReferenceType[] = ['non', 'scor', 'qrr']
export const CHART_VARIANTS: ChartOfAccountsVariant[] = ['kmu', 'kaefer']
export const PERIOD_LOCK_POLICIES: PeriodLockPolicy[] = ['month_end_close', 'quarter_end_close', 'year_end_close']
/** The Rappenrundung steps the API accepts, as the API spells them. */
export const CASH_ROUNDINGS = ['0.01', '0.05', '0.10', '1.00'] as const

const REASON_KEYS: Record<string, string> = {
  confirmation_required: 'business.errors.confirmationRequired',
  unknown_reference_code: 'business.errors.unknownReferenceCode',
  qr_reference_needs_qr_iban: 'business.errors.qrReferenceNeedsQrIban',
  qr_iban_needs_qr_reference: 'business.errors.qrIbanNeedsQrReference',
  creditor_prefix_needs_scor: 'business.errors.creditorPrefixNeedsScor',
  default_conflict: 'business.errors.defaultConflict',
}

/** The one text a Business-screen refusal renders, in the UI language. */
export function businessRefusalText(t: TFunction, err: unknown): string {
  if (!(err instanceof ApiError)) return t('common.requestFailed')
  const reason = err.details?.reason
  if (typeof reason === 'string' && REASON_KEYS[reason]) return t(REASON_KEYS[reason])
  if (err.status === 403) return t('business.errors.managerOnly')
  if (err.status === 409 && err.details && 'uid' in err.details) return t('business.errors.uidTaken')
  if (err.status === 409) return t('business.errors.conflict')
  if (err.status === 422 || err.status === 400) return t('business.errors.invalid')
  return t('common.requestFailed')
}

/** `CH9300762011623852957` → `CH93 0076 2011 6238 5295 7`, as a bank prints it. */
export function formatIban(compact: string): string {
  return compact.replace(/(.{4})/g, '$1 ').trim()
}

/** The API's `MM-DD` as day and month numbers, and back. */
export function splitFiscalYearStart(value: string): { month: string; day: string } {
  const [month, day] = value.split('-')
  return { month: String(Number(month)), day: String(Number(day)) }
}

export function joinFiscalYearStart(month: string, day: string): string {
  return `${month.padStart(2, '0')}-${day.padStart(2, '0')}`
}

/** Month numbers as the fiscal-year selects use them; the names are i18n keys (`business.fiscal.months.N`). */
export const MONTHS = Array.from({ length: 12 }, (_, i) => String(i + 1))

export const DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }))

/** Empty input → null, else the trimmed string — PATCH semantics for a nullable text field. */
export function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}
