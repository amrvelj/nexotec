import { ApiError } from './client'

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError
}
