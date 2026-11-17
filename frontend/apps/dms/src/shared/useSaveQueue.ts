import { useCallback, useRef, useState } from 'react'

export interface DealSaveQueue<D> {
  /**
   * Queues one save of the deal. `send` runs only once every earlier save
   * has settled, and receives the deal as the previous save left it — its
   * `version` is the If-Match to send. The server's updated deal is stored
   * (`onSaved`) before the next save starts. Rejects when the server
   * refuses this save; the saves queued behind it still run, each against
   * the version the last successful save returned. Resolves `undefined`,
   * without sending, while there is no deal.
   */
  save: (send: (current: D) => Promise<D>) => Promise<D | undefined>
  /** Saves queued or on their way to the server. */
  pending: number
}

/**
 * KAN-314 — every route that changes an offer or a pending contract takes
 * the row lock and checks If-Match (KAN-311), so two saves sent with the
 * same version cannot both land: the second is a stale-version 409. The
 * deal workspaces autosave each container on its own, so overlapping saves
 * are routine (a discount typed while a line item is still saving). This
 * queue sends them one after the other, each with the version its
 * predecessor returned.
 *
 * `getDeal` must return the newest copy synchronously — the hosts read the
 * query cache, which `onSaved` writes before the next save runs. A refused
 * save is never retried here: a stale version means someone else changed
 * the deal, and resending would overwrite that change unseen. The host
 * says so and reloads.
 *
 * `pending` replaces KAN-288's in-flight counter on the contract page:
 * Confirm waits while it is above zero.
 */
export function useDealSaveQueue<D extends { id: string; version: number }>(
  getDeal: () => D | undefined,
  onSaved: (deal: D) => void
): DealSaveQueue<D> {
  const tail = useRef<Promise<unknown>>(Promise.resolve())
  const lastSaved = useRef<D | undefined>(undefined)
  const getDealRef = useRef(getDeal)
  getDealRef.current = getDeal
  const onSavedRef = useRef(onSaved)
  onSavedRef.current = onSaved
  const [pending, setPending] = useState(0)

  const save = useCallback((send: (current: D) => Promise<D>): Promise<D | undefined> => {
    setPending((n) => n + 1)
    const run = tail.current.then(async () => {
      const cached = getDealRef.current()
      if (!cached) return undefined
      // A refetch that left before the last save landed can put an older
      // copy back in the cache; never send a version below the one the
      // server last returned to us for this deal.
      const last = lastSaved.current
      const current = last && last.id === cached.id && last.version > cached.version ? last : cached
      const updated = await send(current)
      lastSaved.current = updated
      onSavedRef.current(updated)
      return updated
    })
    // The next save waits for this one to settle, refused or not.
    tail.current = run.catch(() => undefined)
    return run.finally(() => setPending((n) => n - 1))
  }, [])

  return { save, pending }
}
