import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * A section's form state, loaded from a versioned record. `loaded` is the
 * record the form came from: its version is the If-Match, its values are
 * what a change is judged against. When a newer version of the record
 * arrives:
 * - an untouched form takes it over;
 * - a touched form keeps the user's entries, and adopts the new record as
 *   `loaded` when none of THIS section's fields moved between the old
 *   `loaded` and the new record (another section of the same screen saved,
 *   or another user changed something else) — so the save still goes
 *   through with the current version;
 * - when one of this section's own fields did move, `loaded` stays: the
 *   save is then the API's 409, said on screen, never a silent overwrite.
 * `markSaved(record)` takes the saved record as the new `loaded`.
 */
export function useRecordForm<R extends { version: number }, F>(record: R, toForm: (record: R) => F) {
  const [form, setFormState] = useState<F>(() => toForm(record))
  const [loaded, setLoaded] = useState<R>(record)
  const [dirty, setDirty] = useState(false)
  const seen = useRef(record.version)
  useEffect(() => {
    if (record.version === seen.current) return
    seen.current = record.version
    if (!dirty) {
      setFormState(toForm(record))
      setLoaded(record)
      return
    }
    if (JSON.stringify(toForm(loaded)) === JSON.stringify(toForm(record))) setLoaded(record)
  }, [record, dirty, loaded, toForm])
  const set = useCallback(<K extends keyof F>(key: K, value: F[K]) => {
    setFormState((current) => ({ ...current, [key]: value }))
    setDirty(true)
  }, [])
  const reset = useCallback(() => {
    setFormState(toForm(record))
    setLoaded(record)
    seen.current = record.version
    setDirty(false)
  }, [record, toForm])
  /** After a successful save: the saved record is what the form now holds. */
  const markSaved = useCallback(
    (saved: R) => {
      seen.current = saved.version
      setLoaded(saved)
      setFormState(toForm(saved))
      setDirty(false)
    },
    [toForm],
  )
  return { form, set, dirty, reset, markSaved, loaded }
}
