import { ApiError } from '../../api/client'

/** KAN-231 — the API refuses a plate read past the per-user limit with 403
 * and `details.reason` `plate_read_limit_reached`. */
export function isPlateReadLimitError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.details?.reason === 'plate_read_limit_reached'
}
