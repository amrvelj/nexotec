import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

// KAN-233: every screen but the sign-in is its own chunk, fetched when its
// route is first opened, so the first visit (and the first after every
// deploy) no longer downloads the whole application.

/** A screen's chunk could not be fetched — typically a tab opened before a
 * deploy asking for a hashed file the deploy replaced, or the network
 * dropping. Only this error gets the reload screen; any other error thrown
 * while rendering goes on to whatever handles it today. */
export class ChunkLoadError extends Error {
  constructor(cause: unknown) {
    // `cause` keeps the original error's type and stack for the console.
    super(cause instanceof Error ? cause.message : String(cause), { cause })
    this.name = 'ChunkLoadError'
  }
}

/** `React.lazy` for a module's named page export (a route screen takes no
 * props). A failed import rejects with `ChunkLoadError`; React's lazy caches
 * the rejection, which is right: the cure is a reload, never a silent retry.
 * Every rejection counts, not only a failed fetch: a chunk that throws while
 * it is evaluated (a top-level error) also shows the reload screen, which a
 * reload will not cure — it is logged with its cause. Browsers word a failed
 * fetch differently, so the message is not a reliable way to tell them apart. */
export function lazyPage<K extends string, M extends Record<K, ComponentType<object>>>(
  load: () => Promise<M>,
  name: K,
): LazyExoticComponent<ComponentType<object>> {
  return lazy(async () => {
    let module: M
    try {
      module = await load()
    } catch (err) {
      throw new ChunkLoadError(err)
    }
    return { default: module[name] }
  })
}
