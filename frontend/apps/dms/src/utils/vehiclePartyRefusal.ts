import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'

/**
 * KAN-369, KAN-389 — a refused vehicle-party allocation, said in the user's
 * language. The customer's Link vehicle dialog and the vehicle's Identity tab
 * send the same allocation and get the same refusals, so both map them here:
 * by the 409's `details.reason`, or by the status where the API gives none.
 * The API's English message is never shown; anything unknown is the
 * surface's own translated `fallbackKey`.
 */
export function vehiclePartyRefusalMessage(t: TFunction, err: unknown, fallbackKey: string): string {
  if (!(err instanceof ApiError)) return t(fallbackKey)
  if (err.code === 'unknown_error') return t('common.requestFailed')
  if (err.status === 404) return t('customerDetail.linkVehicleRefused.notFound')
  switch (err.details?.reason) {
    case 'customer_merged':
      return t('customerDetail.linkVehicleRefused.customerMerged')
    case 'vehicle_party_exists':
      return t('customerDetail.linkVehicleRefused.roleAlreadyHeld')
    default:
      return t(fallbackKey)
  }
}
