import { ApiError } from '../../api/client'
import i18n from '../../i18n'

/** The password policy's refusal (422 with `details.rule`), a reused
 * password or a dead link (400 with `details.rule`) in the user's language;
 * any other error as the API said it. `invalidLinkKey` is the screen's own
 * wording for a link that no longer works. */
export function passwordErrorMessage(err: unknown, invalidLinkKey = 'auth.reset.invalidLink'): string {
  if (err instanceof ApiError) {
    const rule = err.details?.rule
    if (err.status === 422 && typeof rule === 'string') {
      const minLength = err.details?.minLength
      if (rule === 'min_length') return i18n.t('auth.password.minLength', { count: typeof minLength === 'number' ? minLength : 12 })
      if (rule === 'deny_list') return i18n.t('auth.password.denyList')
      if (rule === 'breached') return i18n.t('auth.password.breached')
    }
    if (err.status === 400 && rule === 'reused') return i18n.t('auth.password.reused')
    if (err.status === 400 && rule === 'link') return i18n.t(invalidLinkKey)
    return err.message
  }
  return i18n.t('common.requestFailed')
}
