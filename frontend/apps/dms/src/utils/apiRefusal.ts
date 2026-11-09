import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'
import i18n, { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatDate, formatDateOnly } from './format'

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
  vin_differs_from_configuration: 'valuationCreate.vinDiffersFromConfiguration',
  ...Object.fromEntries(
    [
      'accept_invitation_required',
      'access_window_closed',
      'accounting_period_closed',
      'already_revoked',
      'brand_code_taken',
      'brand_required',
      'catalogue_filter_value_unknown',
      'catalogue_scope_invalid',
      'catalogue_scope_required',
      'catalogue_variant_missing',
      'category_automatic_only',
      'coded_field_locked',
      'coded_field_required',
      'coded_fields_required',
      'colleague_cannot_act_there',
      'configuration_already_linked',
      'configuration_held_by_stock_item',
      'configuration_mode_fixed',
      'configuration_mode_not_allowed',
      'configuration_vin_differs',
      'cursor_invalid',
      'customer_do_not_contact',
      'customer_tag_exists',
      'discount_suppressed_on_used_vehicle',
      'duplicate_connection',
      'email_already_on_customer',
      'external_id_taken',
      'invalid_new_price',
      'invalid_type_approval_number',
      'last_usable_contact_point',
      'legacy_sale',
      'logo_dimensions_too_large',
      'logo_file_too_large',
      'logo_format_jpeg',
      'logo_format_unsupported',
      'logo_png_damaged',
      'logo_svg_entities',
      'logo_svg_invalid',
      'logo_too_many_pixels',
      'logo_too_small',
      'merged_party_row',
      'missing_config_fields',
      'missing_containers',
      'missing_price',
      'missing_vehicle',
      'no_catalogue_variant',
      'no_fields',
      'no_provider_connection',
      'no_signed_contract',
      'not_active',
      'nul_character',
      'offer_already_contracted',
      'page_invalid',
      'passkey_key_not_accepted',
      'passkey_not_verified',
      'pending_contract_carries_it',
      'phone_already_on_customer',
      'photo_not_an_image',
      'photo_too_large',
      'primary_unset_directly',
      'provider_code_already_mapped',
      'purchase_requires_vin',
      'purchase_vin_mismatch',
      'signed_contract_carries_it',
      'sort_field_unknown',
      'stamp_too_recent',
      'vehicle_kind_unknown_to_provider',
      // KAN-208 — the standard-text library and the documents that print it.
      'standard_text_category_mismatch',
      'standard_text_code_target_mismatch',
      'standard_text_code_unknown',
      'standard_text_exists',
      'standard_text_not_an_override',
      'standard_text_placeholder_invalid',
      'standard_text_placeholder_unresolved',
      'standard_text_seed_language_missing',
      'standard_text_seed_not_shipped',
      'standard_text_slot_unknown',
      'standard_text_target_required',
      'standard_text_target_unknown',
      'standard_text_template_id_required',
      'standard_text_template_whatsapp_only',
      'too_many_images',
      // KAN-574 — a valuation names its car; a trade-in is a car.
      'trade_in_valuation_names_no_car',
      'ui_translation_blank',
      'ui_translation_placeholders_changed',
      'unknown_fields',
      'unrecognised_identifier',
      'use_deactivate_endpoint',
      'valuation_expired',
      'valuation_names_no_car',
      // KAN-253 — «Verwendung aufheben».
      'valuation_not_used',
      'vehicle_label_follows_configuration',
      'vehicle_left_stock',
      'vehicle_not_purchased',
      'vin_already_in_stock',
      'wagenbuch_closed',
      // KAN-204 — Locations and the holiday calendars.
      'bank_account_used_by_location',
      'closure_period_inverted',
      'last_active_location',
      'location_address_incomplete',
      'location_canton_not_swiss',
      'location_canton_required',
      'location_code_immutable',
      'location_code_taken',
      'location_geo_incomplete',
      'location_inactive',
      'location_postal_code_invalid',
      'location_validity_inverted',
      'unknown_bank_account',
      'unknown_holiday_calendar',
      'year_out_of_range',
      // KAN-467 — recording and ending a Kontrollschild.
      'plate_already_ended',
      'plate_already_on_vehicle',
      'plate_canton_invalid',
      'plate_current_on_other_vehicle',
      'plate_invalid',
      'plate_move_and_wechselschild',
      'plate_move_before_start',
      'plate_validity_invalid',
      'plate_wechselschild_not_possible',
      // KAN-207 — number ranges.
      'number_location_code_missing',
      'number_pattern_invalid',
      'number_pattern_location_code_missing',
      'number_pattern_location_unavailable',
      'number_pattern_reset_needs_year',
      'number_pattern_too_long',
      'number_range_counter_too_short',
      'number_range_exhausted',
      'number_range_frozen',
      'number_range_reason_required',
      'number_range_start_out_of_range',
      // KAN-523 — the plate a sold car leaves with, at «Rechnung anfordern».
      'invoice_plate_vehicle_unknown',
      // KAN-539 — «Rechnung anfordern» once per contract.
      'invoice_already_requested',
      // KAN-146 — a purchase booked once, and «Einkauf stornieren».
      'purchase_already_booked',
      'purchase_not_booked',
      'purchase_storno_invoice_requested',
      'purchase_storno_left_stock',
      'source_ref_reserved',
      // KAN-572 — only a trade-in's car can go back to its customer.
      'storno_return_not_trade_in',
      // KAN-552 — the valuation's provider quote, and a valuation built on one.
      'not_entitled',
      'provider_unavailable',
      'provider_cannot_value',
      'provider_quote_required',
      'provider_quote_mismatch',
      'provider_quote_stale',
      'provider_quote_inputs_missing',
    ].map((reason) => [reason, `apiRefusal.reasons.${reason}`]),
  ),
}

// The few reasons whose sentence names a value the refusal carries — said
// in the UI language too (a container's name, a date in the user's locale).
const REASON_PARAMS: Record<string, (details: Details, t: TFunction) => Record<string, unknown>> = {
  access_window_closed: (d, t) => ({ window: accessWindowText(d, t) }),
  configuration_held_by_stock_item: (d) => ({ stockNumber: String(d.stockNumber ?? '—') }),
  credit_block: (d) => ({ reason: String(d.creditBlockReason ?? '—') }),
  // KAN-539: the instant of the first request, as a day in the user's locale.
  invoice_already_requested: (d) => ({
    date:
      typeof d.invoiceRequestedAt === 'string' && !Number.isNaN(Date.parse(d.invoiceRequestedAt))
        ? formatDate(d.invoiceRequestedAt, toSwissLocale(i18n.language as SupportedLanguage))
        : '—',
  }),
  customer_do_not_contact: (d) => ({ customerNumber: String(d.customerNumber ?? '—') }),
  // KAN-552: the provider's own reason, as delivered (ADR-044).
  provider_cannot_value: (d) => ({ message: typeof d.providerMessage === 'string' ? d.providerMessage : '—' }),
  // KAN-146: the contract whose invoice request keeps the purchase booked.
  purchase_storno_invoice_requested: (d) => ({ contractNumber: String(d.contractNumber ?? '—') }),
  // A connection's settings by their labels on the connect form; a key the
  // bundles do not name yet as the API spells it, never the missing-key marker.
  missing_config_fields: (d, t) => ({
    fields: listed(d.missingConfigKeys, (key) =>
      i18n.exists(`integrationEnums.configKey.${key}`) ? t(`integrationEnums.configKey.${key}`) : key,
    ),
  }),
  // KAN-205: the upload's pixel size and the minimum it falls short of.
  logo_dimensions_too_large: (d) => ({ max: typeof d.maxLongEdge === 'number' ? d.maxLongEdge : 10000 }),
  logo_too_many_pixels: (d) => ({
    width: typeof d.width === 'number' ? d.width : '—',
    height: typeof d.height === 'number' ? d.height : '—',
    max: typeof d.maxMegapixels === 'number' ? d.maxMegapixels : 16,
  }),
  logo_too_small: (d) => ({
    width: typeof d.width === 'number' ? d.width : '—',
    height: typeof d.height === 'number' ? d.height : '—',
    min: typeof d.minLongEdge === 'number' ? d.minLongEdge : 600,
  }),
  missing_containers: (d, t) => ({
    containers: Array.isArray(d.missingContainers)
      ? d.missingContainers.map((id) => t(`offerWorkspace.containers.${id === 'trade_in' ? 'tradeIn' : String(id)}`)).join(', ')
      : '—',
  }),
  offer_has_contract: (d) => ({ contractNumber: String(d.contractNumber ?? '—') }),
  provider_code_already_mapped: (d) => ({
    mapping: d.canonicalListCode != null && d.canonicalValueCode != null ? `${String(d.canonicalListCode)}/${String(d.canonicalValueCode)}` : '—',
  }),
  // KAN-208 (A-RULE-4): the tokens at fault, as the text spells them.
  standard_text_placeholder_invalid: (d) => ({ placeholders: placeholderTokens(d.placeholders) }),
  standard_text_placeholder_unresolved: (d) => ({ placeholders: placeholderTokens(d.placeholders) }),
  too_many_images: (d) => ({ max: typeof d.maxImages === 'number' ? d.maxImages : 16 }),
  vin_already_in_stock: (d) => ({ stockNumber: String(d.stockNumber ?? '—') }),
}

const placeholderTokens = (value: unknown): string =>
  Array.isArray(value) && value.length > 0
    ? value.map((item) => (item && typeof item === 'object' && 'token' in item && item.token ? `{{${String(item.token)}}}` : '{{ }}')).join(', ')
    : '—'

const listed = (value: unknown, label: (item: string) => string = (item) => item): string =>
  Array.isArray(value) && value.length > 0 ? value.map((item) => label(String(item))).join(', ') : '—'

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

// KAN-472 — the acceptance's 403 names the user's access window (two
// date-only values, `user.valid_from`/`valid_to`): "from …", "until …" or
// both, each a calendar day in the user's locale.
function accessWindowText(details: Details, t: TFunction): string {
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  // Only a real ISO calendar day is formatted: anything else names no day
  // (and formatDateOnly would throw on it inside the refusal path).
  const day = (value: unknown): string | null =>
    typeof value === 'string' && ISO_DAY.test(value) && !Number.isNaN(Date.parse(value)) ? formatDateOnly(value, locale) : null
  const from = day(details.validFrom)
  const to = day(details.validTo)
  if (from && to) return t('apiRefusal.accessWindow.between', { from, to })
  if (from) return t('apiRefusal.accessWindow.from', { from })
  if (to) return t('apiRefusal.accessWindow.until', { to })
  return t('apiRefusal.accessWindow.none')
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
  if (isStaleVersion(err)) return t('apiRefusal.staleVersion')
  if (typeof details.reason === 'string') {
    const key = refusalReasonKey(details.reason)
    if (key) return t(key, REASON_PARAMS[details.reason]?.(details, t) ?? {})
  }
  if (err.status === 403) return t('apiRefusal.forbidden')
  if (err.status === 404) return t('apiRefusal.notFound')
  if (err.status === 422 && Array.isArray(details.errors)) return t('apiRefusal.invalid')
  return t(fallbackKey)
}

/** A stale If-Match refusal: a 409 that names the record's current version
 * (`app/core/concurrency.py`). Only this one offers a reload — a 409 for a
 * business rule (a phone already on the customer) is not fixed by one. */
export function isStaleVersion(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.details?.currentVersion != null
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
