import { useCallback, useMemo, useRef } from 'react'

/**
 * KAN-119 — one `Idempotency-Key` per form submission.
 *
 * The backend replays the stored response when a POST arrives again under the
 * same key, and refuses a concurrent twin, so a double click or a retry after a
 * timeout never creates a second record. That holds only if the retry carries
 * the SAME key: a key minted per request (`crypto.randomUUID()` at the call
 * site) makes every retry look like a new submission.
 *
 * `headers()` returns the current submission's key, minting it on first use; it
 * stays the same across retries and re-renders. Call `renew()` once the
 * submission has succeeded and whenever the form is opened afresh, so the next
 * submission gets a key of its own. A failed submission keeps its key: the
 * server releases the key of a request that failed, so the corrected form is
 * sent again under it.
 */
export function useIdempotencyKey(): { headers: () => Record<string, string>; renew: () => void } {
  const key = useRef<string | null>(null)
  const headers = useCallback(() => {
    key.current ??= crypto.randomUUID()
    return { 'Idempotency-Key': key.current }
  }, [])
  const renew = useCallback(() => {
    key.current = null
  }, [])
  return useMemo(() => ({ headers, renew }), [headers, renew])
}
