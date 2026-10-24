import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * A section's form state, loaded from a versioned record: when a newer
 * version of the record arrives (another section saved, or another tab),
 * an untouched form takes it over; a touched one keeps the user's entries
 * until they save or reset (a conflict is then the API's 409, said on
 * screen).
 */
export function useRecordForm<R extends { version: number }, F>(record: R, toForm: (record: R) => F) {
  const [form, setFormState] = useState<F>(() => toForm(record))
  // The record the form was loaded from: its version is the If-Match and its
  // values are what a change is judged against — never the newest record,
  // which would turn another user's change into "ours" and lose the 409.
  const [loaded, setLoaded] = useState<R>(record)
  const [dirty, setDirty] = useState(false)
  const seen = useRef(record.version)
  useEffect(() => {
    if (record.version !== seen.current) {
      seen.current = record.version
      if (!dirty) {
        setFormState(toForm(record))
        setLoaded(record)
      }
    }
  }, [record, dirty, toForm])
  const set = useCallback(<K extends keyof F>(key: K, value: F[K]) => {
    setFormState((current) => ({ ...current, [key]: value }))
    setDirty(true)
  }, [])
  const reset = useCallback(() => {
    setFormState(toForm(record))
    setLoaded(record)
    setDirty(false)
  }, [record, toForm])
  /** After a successful save: the record's new version arrives next and takes over. */
  const markSaved = useCallback(() => setDirty(false), [])
  return { form, set, dirty, reset, markSaved, loaded }
}
