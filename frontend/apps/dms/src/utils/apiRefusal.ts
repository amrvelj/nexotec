import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'

type Details = Record<string, unknown>

/**
 * KAN-437 — every `details.reason` the API states, and the key that says it
 * in the UI language. A reason whose wording a screen already had (merge,
 * vehicle roles, contract confirmation, business master data) keeps that
 * key; the rest live under `apiRefusal.reasons`. `apiRefusal.test.ts`
 * fails when the API raises a literal reason this table does not know.
 */
const REASON_KEYS: Record<string, string> = {
  bad_status: 'contractDetail.errors.confirmRefused.badStatus',
  chassis_number_requires_pre_1981_registration: 'common.chassisNumberNeedsPre1981',
  confirmation_required: 'business.errors.confirmationRequired',
  contract_not_pending: 'contractDetail.errors.editRefused.notPending',
  contract_priced_without_inputs: 'contractDetail.edit.pricedWithoutInputs',
  creditor_prefix_needs_scor: 'business.errors.creditorPrefixNeedsScor',
  credit_block: 'contractDetail.errors.confirmRefused.creditBlock',
  customer_already_merged: 'customerDetail.mergeRefused.alreadyMerged',
  customer_merged: 'customerDetail.linkVehicleRefused.customerMerged',
  default_conflict: 'business.errors.defaultConflict',
  do_not_contact: 'contractDetail.errors.confirmRefused.doNotContact',
  invalid_vin: 'common.invalidVin',
  last_manager: 'usersAdmin.errors.lastManager',
  last_manager_elsewhere: 'usersAdmin.errors.lastManagerElsewhere',
  merge_conflict_on_survivor: 'customerDetail.mergeRefused.survivorConflict',
  merge_into_self: 'customerDetail.mergeRefused.intoSelf',
  merge_target_merged: 'customerDetail.mergeRefused.targetMerged',
  merge_vehicle_party_busy: 'customerDetail.mergeRefused.vehiclePartyBusy',
  missing_address: 'contractDetail.errors.confirmRefused.missingAddress',
  missing_customer: 'contractDetail.errors.confirmRefused.missingCustomer',
  no_best_match: 'configurator.bestMatch.error',
  offer_has_contract: 'offerWorkspace.errors.saveRefused.offerHasContract',
  plate_read_limit_reached: 'vehicleDetail.plates.limitReached',
  qr_iban_needs_qr_reference: 'business.errors.qrIbanNeedsQrReference',
  qr_reference_needs_qr_iban: 'business.errors.qrReferenceNeedsQrIban',
  trade_in_valuation_expired: 'contractDetail.errors.confirmRefused.tradeInValuationExpired',
  unknown_reference_code: 'business.errors.unknownReferenceCode',
  vehicle_party_exists: 'customerDetail.linkVehicleRefused.roleAlreadyHeld',
  ...Object.fromEntries(
    [
      'accept_invitation_required',
      'access_window_closed',
      'already_revoked',
      'catalogue_variant_missing',
      'coded_field_locked',
      'coded_field_required',
      'coded_fields_required',
      'colleague_cannot_act_there',
      'configuration_mode_fixed',
      'configuration_mode_not_allowed',
      'customer_tag_exists',
      'duplicate_connection',
      'invalid_new_price',
      'invalid_type_approval_number',
      'legacy_sale',
      'merged_party_row',
      'missing_price',
      'missing_vehicle',
      'no_catalogue_variant',
      'no_fields',
      'no_provider_connection',
      'no_signed_contract',
      'not_active',
      'nul_character',
      'offer_already_contracted',
      'unknown_fields',
      'unrecognised_identifier',
      'use_deactivate_endpoint',
      'valuation_expired',
      'vehicle_left_stock',
      'vehicle_not_purchased',
      'vin_already_in_stock',
    ].map((reason) => [reason, `apiRefusal.reasons.${reason}`]),
  ),
}

// The few reasons whose sentence names a value the refusal carries.
const REASON_PARAMS: Record<string, (details: Details) => Record<string, unknown>> = {
  credit_block: (d) => ({ reason: String(d.creditBlockReason ?? '—') }),
  offer_has_contract: (d) => ({ contractNumber: String(d.contractNumber ?? '—') }),
  vin_already_in_stock: (d) => ({ stockNumber: String(d.stockNumber ?? '—') }),
}

/** The reasons this table translates — for its own tests. */
export const TRANSLATED_REFUSAL_REASONS: readonly string[] = Object.keys(REASON_KEYS)

/** The key of a translated `details.reason`, or null for one the bundles do not know. */
export function refusalReasonKey(reason: string): string | null {
  return Object.prototype.hasOwnProperty.call(REASON_KEYS, reason) ? REASON_KEYS[reason] : null
}

/**
 * An API refusal in the UI language (KAN-398's rule, KAN-437): a stale
 * If-Match 409 (`details.currentVersion`) and a known `details.reason` say
 * what happened; a 403 (a role's refusal), a 404 and a schema 422 (the
 * request validation's `details.errors`) say that much; anything else — a
 * business rule refused without a reason included, and an error that is not
 * the API's — is the screen's own `fallbackKey`. A response without the
 * API's error body (a gateway's 504, KAN-271) is a failed request. The API's
 * English `message` is never shown: it is for logs.
 */
export function apiRefusalText(t: TFunction, err: unknown, fallbackKey: string): string {
  if (!(err instanceof ApiError)) return t(fallbackKey)
  if (err.code === 'unknown_error') return t('common.requestFailed')
  const details: Details = err.details ?? {}
  if (err.status === 409 && details.currentVersion != null) return t('apiRefusal.staleVersion')
  if (typeof details.reason === 'string') {
    const key = refusalReasonKey(details.reason)
    if (key) return t(key, REASON_PARAMS[details.reason]?.(details) ?? {})
  }
  if (err.status === 403) return t('apiRefusal.forbidden')
  if (err.status === 404) return t('apiRefusal.notFound')
  if (err.status === 422 && Array.isArray(details.errors)) return t('apiRefusal.invalid')
  return t(fallbackKey)
}

/** The field a schema-level 422 names (the last body location), or null. */
export function refusedField(err: ApiError): string | null {
  const errors = err.details?.errors
  if (!Array.isArray(errors)) return null
  for (const entry of errors) {
    const loc = (entry as { loc?: unknown } | null)?.loc
    if (Array.isArray(loc) && loc[0] === 'body' && typeof loc[loc.length - 1] === 'string') return loc[loc.length - 1] as string
  }
  return null
}
