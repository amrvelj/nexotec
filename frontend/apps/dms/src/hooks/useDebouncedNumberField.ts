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
 * KAN-480 — the field takes the input's own value, a number or the text of
 * a partial entry (Mantine hands a lone «-» over as the string "-"), and
 * shows it as typed. A value that does not read as a finite number is never
 * committed: as NaN it would be sent as `null` (`JSON.stringify(NaN)`) and
 * clear the saved figure. It counts as unsaved until it is finished into a
 * number or emptied, `invalid` says so, and a newer server copy does not
 * replace it.
 *
 * The input itself empties a lone «-» when it loses focus (react-number-
 * format, under Mantine's NumberInput: it emits '' and then its blur). That
 * '' is the input tidying up, not the user clearing the figure, so the host
 * passes `onBlur` and the hook puts the partial entry back instead of
 * committing a clear. An empty field the user made by deleting stays empty.
 *
 * Returns `[value, setValue, unsaved, invalid, onBlur]` — `unsaved` is true
 * while a typed value waits out the delay or is not a number (KAN-288);
 * `invalid` is true while the field holds something that is not a number, for
 * the host's `error` (`common.notYetANumber`); `onBlur` goes on the input.
 */
export function useDebouncedNumberField(
  serverValue: number | null,
  resetKey: unknown,
  onCommit: (value: number | null) => void | Promise<unknown>,
  delay = 500
): [number | string, (value: number | string) => void, boolean, boolean, () => void] {
  const next: number | '' = serverValue ?? ''
  // What the input shows: the server's figure, or what was typed as typed.
  const [local, setLocal] = useState<number | string>(next)
  // What was last handed to `onCommit` or taken from the server — state for
  // `unsaved`, a ref for the callbacks that run between renders.
  const [committed, setCommitted] = useState<number | ''>(next)
  const localRef = useRef<number | string>(next)
  const committedRef = useRef<number | ''>(next)
  const serverRef = useRef<number | ''>(next)
  serverRef.current = next
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savesOutRef = useRef(0)
  // The partial entry an emptying change replaced, until the end of the
  // current event: the input's own blur tidy-up arrives just before its blur.
  const emptiedPartialRef = useRef<string | null>(null)
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
  // typed value waiting (Object.is: a lone "-" reads as NaN, and NaN !== NaN)
  // and no save out.
  const adoptServerCopy = useCallback(() => {
    if (timerRef.current !== null || savesOutRef.current > 0) return
    if (!Object.is(asFigure(localRef.current), committedRef.current)) return
    if (Object.is(serverRef.current, committedRef.current)) return
    show(serverRef.current)
  }, [])

  const commit = useCallback(() => {
    timerRef.current = null
    const value = asFigure(localRef.current)
    if (Object.is(value, committedRef.current)) return
    // Not yet a number (KAN-480): kept in the field, never sent.
    if (!isCommittable(value)) return
    committedRef.current = value
    setCommitted(value)
    const saving = onCommitRef.current(value === '' ? null : value)
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
    (value: number | string) => {
      const previous = localRef.current
      if (value === '' && typeof previous === 'string' && !isCommittable(asFigure(previous))) {
        emptiedPartialRef.current = previous
        queueMicrotask(() => {
          emptiedPartialRef.current = null
        })
      }
      localRef.current = value
      setLocal(value)
      if (timerRef.current !== null) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(commit, delay)
    },
    [commit, delay]
  )

  const onBlur = useCallback(() => {
    const partial = emptiedPartialRef.current
    if (partial === null || localRef.current !== '') return
    emptiedPartialRef.current = null
    if (timerRef.current !== null) clearTimeout(timerRef.current)
    timerRef.current = null
    localRef.current = partial
    // A tick later: set in the same event as the input's own '', the two
    // updates batch to the value the input already had, and it would keep
    // showing its emptied text.
    setTimeout(() => {
      if (localRef.current === partial) setLocal(partial)
    }, 0)
  }, [])

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
  // Object.is: a value that is not a number reads as NaN, never equal to a
  // committed figure, so it stays "unsaved" until finished or emptied.
  const figure = asFigure(local)
  return [local, setValue, !Object.is(figure, committed), !isCommittable(figure), onBlur]
}

function isCommittable(figure: number | ''): boolean {
  return figure === '' || Number.isFinite(figure)
}

/**
 * The figure an input value stands for: '' for an empty field, else the
 * number it reads as — NaN for a partial entry such as a lone «-».
 */
export function asFigure(value: number | string): number | '' {
  if (typeof value === 'number') return value
  return value.trim() === '' ? '' : Number(value)
}
