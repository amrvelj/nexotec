import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * KAN-425 — `useDebouncedNumberField`'s idea (KAN-8) for free text. A
 * textarea bound to the query cache with a save on every keystroke shows
 * the old server value until each save returns, so the next keystroke is
 * typed against it and characters are lost. Here local state is what the
 * field shows; `onCommit` runs once the user pauses for `delay`ms, when
 * `flush` is called (the field is left), or when the field unmounts with
 * a draft still waiting — never once per keystroke.
 *
 * `resetKey` re-syncs the draft from `serverValue` only when it changes (a
 * different row arriving, or the host discarding the draft after a refused
 * save), never on this field's own saves coming back.
 *
 * Returns `[value, setValue, flush]`.
 */
export function useDebouncedTextField(
  serverValue: string,
  resetKey: unknown,
  onCommit: (value: string) => void,
  delay = 500
): [string, (value: string) => void, () => void] {
  const [local, setLocal] = useState(serverValue)
  const localRef = useRef(serverValue)
  const committedRef = useRef(serverValue)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const resetKeyRef = useRef(resetKey)
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
    onCommitRef.current(value)
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
    localRef.current = serverValue
    committedRef.current = serverValue
    setLocal(serverValue)
    // Only re-syncs when `resetKey` itself changes — see the docstring for
    // why `serverValue` is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  // A draft still waiting when the field goes away is saved, not dropped.
  useEffect(() => flush, [flush])

  return [local, setValue, flush]
}
