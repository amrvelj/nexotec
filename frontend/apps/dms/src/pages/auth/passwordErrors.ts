import { ApiError } from '../../api/client'
import i18n from '../../i18n'

/** The password policy's refusal (422 with `details.rule`) in the user's
 * language; any other error as the API said it. */
export function passwordErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const rule = err.details?.rule
    if (err.status === 422 && typeof rule === 'string') {
      const minLength = err.details?.minLength
      if (rule === 'min_length') return i18n.t('auth.password.minLength', { count: typeof minLength === 'number' ? minLength : 12 })
      if (rule === 'deny_list') return i18n.t('auth.password.denyList')
      if (rule === 'breached') return i18n.t('auth.password.breached')
    }
    if (err.status === 400) return i18n.t('auth.password.reused')
    return err.message
  }
  return i18n.t('common.requestFailed')
}
