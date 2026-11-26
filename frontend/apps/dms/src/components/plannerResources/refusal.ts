import { useCallback, useState } from 'react'
import { useInlineEditRefusal } from '../../hooks/useInlineEditRefusal'
import { isStaleVersion } from '../../utils/apiRefusal'

/** A list entry's name in the UI language (ruling 176): its translation, else its name. */
export function entryName(entry: { name: string; nameTranslations: Record<string, string> }, language: string): string {
  const translated = entry.nameTranslations[language.slice(0, 2)]
  return translated && translated.trim() !== '' ? translated : entry.name
}

export interface Refusal {
  text: string
  /** A stale If-Match 409: the screen offers a reload (ui-forms.md). */
  stale: boolean
}

/**
 * KAN-808 — a refused write as the planner-resources screen shows it: the
 * refusal in the UI language through the app's one helper (`apiRefusalText`,
 * by way of `useInlineEditRefusal`), and whether only a reload can help.
 */
export function useRefusal(fallbackKey = 'plannerResources.saveError') {
  const { describeError, reloadLabel } = useInlineEditRefusal(fallbackKey)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const refuse = useCallback(
    (err: unknown) => setRefusal({ text: describeError(err), stale: isStaleVersion(err) }),
    [describeError],
  )
  const clear = useCallback(() => setRefusal(null), [])
  return { refusal, refuse, clear, reloadLabel }
}
