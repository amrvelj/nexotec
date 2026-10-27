import { useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { apiRefusalText } from '../utils/apiRefusal'

/**
 * KAN-473 — what the kit's `InlineEditField` says when a save is refused,
 * in the UI language: the refusal through `apiRefusalText` (a stale If-Match
 * 409, a known `details.reason`, a 403/404/schema 422), anything else as
 * `fallbackKey`; and its reload label. The kit ships no wording of its own,
 * so every inline field takes `describeError` (and `reloadLabel` beside an
 * `onReload`) from here.
 */
export function useInlineEditRefusal(fallbackKey = 'common.inlineEdit.saveFailed') {
  const { t } = useTranslation()
  const describeError = useCallback((err: unknown) => apiRefusalText(t, err, fallbackKey), [t, fallbackKey])
  return useMemo(() => ({ describeError, reloadLabel: t('common.inlineEdit.reload') }), [describeError, t])
}
