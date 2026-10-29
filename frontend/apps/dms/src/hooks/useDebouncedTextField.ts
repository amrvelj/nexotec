import { useCallback, useEffect, useRef, useState } from 'react'
import { isNewerVersion } from './useDebouncedNumberField'

/**
 * KAN-425 — `useDebouncedNumberField`'s idea (KAN-8) for free text. A
 * textarea bound to the query cache with a save on every keystroke shows
 * the old server value until each save returns, so the next keystroke is
 * typed against it and characters are lost. Here local state is what the
 * field shows; `onCommit` runs once the user pauses for `delay`ms, when
 * `flush` is called (the field is left), or when the field unmounts with
 * a draft still waiting — never once per keystroke.
 *
 * A newer `serverValue` (a refetch carrying someone else's save) is shown
 * while nothing of this field's is pending — no draft waiting and no save
 * still at the server (`onCommit`'s promise not yet settled) — otherwise
 * the next edit would be sent on top of a text the user never saw, with the
 * refreshed version as its If-Match, and overwrite it without a conflict.
 * While a save is out, an earlier save's echo landing must not put older
 * text back into the field. `resetKey` discards a waiting draft too (a
 * different row arriving, or the host dropping drafts after a refused save).
 *
 * KAN-478 — given the row's `version` as `serverVersion`, a server copy is
 * taken only when its version is above the highest the field has seen: a
 * refetch sent before the field's save that answers after it settled
 * carries the older text and the older version, and is ignored. Without
 * `serverVersion` the field behaves as before.
 *
 * Returns `[value, setValue, flush]`.
 */
export function useDebouncedTextField(
  serverValue: string,
  resetKey: unknown,
  onCommit: (value: string) => void | Promise<unknown>,
  delay = 500,
  serverVersion?: number | null
): [string, (value: string) => void, () => void] {
  const [local, setLocal] = useState(serverValue)
  const localRef = useRef(serverValue)
  const committedRef = useRef(serverValue)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savesOutRef = useRef(0)
  const resetKeyRef = useRef(resetKey)
  // The highest row version this field has seen (KAN-478).
  const highestVersionRef = useRef(serverVersion ?? null)
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit

  const cancelTimer = () => {
    if (timerRef.current === null) return
    clearTimeout(timerRef.current)
    timerRef.current = null
  }

  const flush = useCallback(() => {
    cancelTimer()
    const value = localRef.current
    if (value === committedRef.current) return
    committedRef.current = value
    const saving = onCommitRef.current(value)
    if (saving) {
      savesOutRef.current += 1
      void saving.finally(() => {
        savesOutRef.current -= 1
      })
    }
  }, [])

  const setValue = useCallback(
    (value: string) => {
      localRef.current = value
      setLocal(value)
      cancelTimer()
      timerRef.current = setTimeout(flush, delay)
    },
    [delay, flush]
  )

  useEffect(() => {
    if (resetKeyRef.current === resetKey) return
    resetKeyRef.current = resetKey
    cancelTimer()
    highestVersionRef.current = serverVersion ?? null
    localRef.current = serverValue
    committedRef.current = serverValue
    setLocal(serverValue)
    // Only re-syncs when `resetKey` itself changes — see the docstring for
    // why `serverValue` is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  useEffect(() => {
    const version = serverVersion ?? null
    const isNewer = isNewerVersion(version, highestVersionRef.current)
    if (isNewer) highestVersionRef.current = version
    if (timerRef.current !== null || savesOutRef.current > 0 || localRef.current !== committedRef.current) return
    if (serverValue === committedRef.current || !isNewer) return
    localRef.current = serverValue
    committedRef.current = serverValue
    setLocal(serverValue)
  }, [serverValue, serverVersion])

  // A draft still waiting when the field goes away is saved, not dropped.
  useEffect(() => flush, [flush])

  return [local, setValue, flush]
}
