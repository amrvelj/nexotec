import { ApiError } from '../../api/client'
import { CANTON_OPTIONS } from '../../customerOptions'

/** One car of the group the plate is current on, from the 409's details. */
export interface PlateHolder {
  plateId: string
  vehicleId: string
  vehicleNumber: string | null
  vin: string | null
  validFrom: string
  version: number
}

export const CANTON_CODES = CANTON_OPTIONS.map((option) => option.value)

/** The cars a 409 `plate_current_on_other_vehicle` names — also the refusal
 * of «Rechnung anfordern» with a plate (KAN-523). */
export function holdersOf(err: unknown): PlateHolder[] | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null
  if (err.details?.reason !== 'plate_current_on_other_vehicle') return null
  return (err.details.holders as PlateHolder[] | undefined) ?? null
}
