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
 * `resetKey` re-syncs local state from `serverValue` when it changes (e.g.
 * a different offer's `id`), dropping a waiting value.
 *
 * KAN-442 — a newer `serverValue` (a refetch carrying someone else's save,
 * or the offer reloaded after a refused save) is shown while nothing of this
 * field's is pending: no typed value waiting out the delay and no save still
 * at the server (`onCommit`'s promise not yet settled). The save queue
 * (KAN-314) takes If-Match from the refreshed cache, so a field that kept
 * the old figure would send the next edit on top of a value the user never
 * saw and overwrite it without the 409 If-Match exists to give. A value
 * being typed is never replaced; while a save is out, an earlier echo must
 * not put an older figure back (KAN-425's rule in `useDebouncedTextField`).
 *
 * Returns `[value, setValue, unsaved]` — `unsaved` is true while a typed
 * value waits out the delay (KAN-288).
 */
export function useDebouncedNumberField(
  serverValue: number | null,
  resetKey: unknown,
  onCommit: (value: number | null) => void | Promise<unknown>,
  delay = 500
): [number | '', (value: number | '') => void, boolean] {
  const [local, setLocal] = useState<number | ''>(serverValue ?? '')
  const [debounced] = useDebouncedValue(local, delay)
  const committedRef = useRef<number | ''>(serverValue ?? '')
  const savesOutRef = useRef(0)
  const resetKeyRef = useRef(resetKey)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit

  useEffect(() => {
    if (resetKeyRef.current === resetKey) return
    resetKeyRef.current = resetKey
    setLocal(serverValue ?? '')
    committedRef.current = serverValue ?? ''
    // Only re-syncs when `resetKey` itself changes; a newer `serverValue`
    // of the same row is the effect below's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  useEffect(() => {
    if (debounced === committedRef.current) return
    committedRef.current = debounced
    const saving = onCommitRef.current(debounced === '' ? null : Number(debounced))
    if (saving) {
      savesOutRef.current += 1
      void saving.finally(() => {
        savesOutRef.current -= 1
      })
    }
  }, [debounced])

  const next = serverValue ?? ''
  useEffect(() => {
    // Object.is: a lone "-" is NaN, and NaN !== NaN.
    const pending = !Object.is(local, committedRef.current) || !Object.is(local, debounced)
    if (pending || savesOutRef.current > 0 || Object.is(next, committedRef.current)) return
    committedRef.current = next
    setLocal(next)
    // Runs when the server copy changes, never on a keystroke (`local` is
    // read, not watched: typing must not re-adopt the server value).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [next])

  // KAN-288 — the third value: a typed value not yet handed to `onCommit`.
  // A host that ends the edit (signing a contract) waits for it, or the
  // value would be dropped when the field unmounts and its timer is cleared.
  // Object.is: a lone "-" parses to NaN, and NaN !== NaN would read as
  // "unsaved" forever.
  return [local, setLocal, !Object.is(local, debounced)]
}
