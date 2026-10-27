import { useCallback, useEffect, useRef, useState } from 'react'

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
 * field's is pending: no typed value waiting to be committed and no save
 * still at the server (`onCommit`'s promise not yet settled). The save queue
 * (KAN-314) takes If-Match from the refreshed cache, so a field that kept
 * the old figure would send the next edit on top of a value the user never
 * saw and overwrite it without the 409 If-Match exists to give. A value
 * being typed is never replaced; while a save is out, an earlier echo must
 * not put an older figure back (KAN-425's rule in `useDebouncedTextField`).
 * When a save is refused (its promise rejects; the field handles that), the
 * field shows the server copy it holds at once — one that arrived while the
 * save was out was skipped then, and a reload that brings the same copy
 * changes nothing to react to.
 *
 * The commit runs from a timer on the typed value (as in
 * `useDebouncedTextField`), not from a change of a debounced copy: typing
 * back the figure shown before an adoption is still saved.
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
  const next: number | '' = serverValue ?? ''
  const [local, setLocal] = useState<number | ''>(next)
  // What was last handed to `onCommit` or taken from the server — state for
  // `unsaved`, a ref for the callbacks that run between renders.
  const [committed, setCommitted] = useState<number | ''>(next)
  const localRef = useRef<number | ''>(next)
  const committedRef = useRef<number | ''>(next)
  const serverRef = useRef<number | ''>(next)
  serverRef.current = next
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savesOutRef = useRef(0)
  const resetKeyRef = useRef(resetKey)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit

  const show = (value: number | '') => {
    localRef.current = value
    committedRef.current = value
    setLocal(value)
    setCommitted(value)
  }

  // Takes the server's copy when nothing of this field's is pending: no
  // typed value waiting (Object.is: a lone "-" is NaN, and NaN !== NaN) and
  // no save out.
  const adoptServerCopy = useCallback(() => {
    if (timerRef.current !== null || savesOutRef.current > 0) return
    if (!Object.is(localRef.current, committedRef.current)) return
    if (Object.is(serverRef.current, committedRef.current)) return
    show(serverRef.current)
  }, [])

  const commit = useCallback(() => {
    timerRef.current = null
    const value = localRef.current
    if (Object.is(value, committedRef.current)) return
    committedRef.current = value
    setCommitted(value)
    const saving = onCommitRef.current(value === '' ? null : Number(value))
    if (saving) {
      savesOutRef.current += 1
      saving.then(
        () => {
          savesOutRef.current -= 1
        },
        () => {
          // Refused: the host shows why and reloads; the field shows the
          // server copy it holds, whichever arrived while the save was out.
          savesOutRef.current -= 1
          adoptServerCopy()
        },
      )
    }
  }, [adoptServerCopy])

  const setValue = useCallback(
    (value: number | '') => {
      localRef.current = value
      setLocal(value)
      if (timerRef.current !== null) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(commit, delay)
    },
    [commit, delay]
  )

  useEffect(() => {
    if (resetKeyRef.current === resetKey) return
    resetKeyRef.current = resetKey
    if (timerRef.current !== null) clearTimeout(timerRef.current)
    timerRef.current = null
    show(serverRef.current)
  }, [resetKey])

  // Runs when the server copy changes, never on a keystroke.
  useEffect(adoptServerCopy, [next, adoptServerCopy])

  // A value still waiting when the field goes away is dropped, as before:
  // a host that ends the edit waits for `unsaved` (KAN-288).
  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    },
    []
  )

  // KAN-288 — the third value: a typed value not yet handed to `onCommit`.
  // A host that ends the edit (signing a contract) waits for it, or the
  // value would be dropped when the field unmounts and its timer is cleared.
  // Object.is: a lone "-" parses to NaN, and NaN !== NaN would read as
  // "unsaved" forever.
  return [local, setValue, !Object.is(local, committed)]
}
