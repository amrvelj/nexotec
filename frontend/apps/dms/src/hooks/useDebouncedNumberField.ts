import { useEffect, useRef, useState } from 'react'
import { useDebouncedValue } from '@mantine/hooks'

/**
 * KAN-8 — a `NumberInput` bound directly to a server-derived value (e.g.
 * `offer.manualBasePrice`) with `onChange` calling straight through to an
 * autosave PATCH, on every keystroke, races itself: typing "10" fires a
 * PATCH after "1" and another after "0" before the first has resolved,
 * both carrying the SAME (now stale) `If-Match` version. The second is
 * rejected as a 409 conflict, `patchOffer` has no `.catch()`, and the
 * input — bound straight to the query cache — snaps back to whatever the
 * first, successful PATCH set: the digit typed second is silently lost.
 *
 * This hook makes local state the source of truth for what's displayed
 * while typing, and only actually commits (calls `onCommit`) once the
 * user pauses for `delay`ms — so overlapping in-flight commits can no
 * longer race each other, by construction, not by chance timing.
 *
 * `resetKey` re-syncs local state from `serverValue` only when it
 * changes (e.g. a different offer's `id`) — NOT on every incidental
 * round-trip of this same field's own value, which would reintroduce the
 * exact same "server value arrives mid-edit and clobbers what's being
 * typed" failure mode this hook exists to close.
 */
export function useDebouncedNumberField(
  serverValue: number | null,
  resetKey: unknown,
  onCommit: (value: number | null) => void,
  delay = 500
): [number | '', (value: number | '') => void] {
  const [local, setLocal] = useState<number | ''>(serverValue ?? '')
  const [debounced] = useDebouncedValue(local, delay)
  const committedRef = useRef<number | ''>(serverValue ?? '')
  const resetKeyRef = useRef(resetKey)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit

  useEffect(() => {
    if (resetKeyRef.current === resetKey) return
    resetKeyRef.current = resetKey
    setLocal(serverValue ?? '')
    committedRef.current = serverValue ?? ''
    // Only re-syncs when `resetKey` itself changes — see the hook's own
    // docstring for why `serverValue` is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  useEffect(() => {
    if (debounced === committedRef.current) return
    committedRef.current = debounced
    onCommitRef.current(debounced === '' ? null : Number(debounced))
  }, [debounced])

  return [local, setLocal]
}
