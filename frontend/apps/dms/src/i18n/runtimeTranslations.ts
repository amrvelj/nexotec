import type { ResourceKey } from 'i18next'
import { API_BASE_URL } from '../api/client'
import i18n, { runtimeTranslationHooks, storedUiLanguage, SUPPORTED_LANGUAGES, type SupportedLanguage } from './index'

/**
 * KAN-209 (D-A-23, FR-A-30…32) — UI strings at runtime.
 *
 * The platform publishes a value for a key in a language through the
 * Translations panel; `GET /v1/i18n/{language}` serves what it published.
 * Resolution, per key: the published value → the shipped bundle (which stays
 * the fallback and keeps shipping) → English's (`fallbackLng`) → the loud
 * marker, which is reported to the `missing_translation` log.
 *
 * Loading (FR-A-31): the start language's map is fetched beside its bundle, and
 * main.tsx paints once both are in — or after `FIRST_PAINT_BUDGET_MS` at the
 * latest, so a slow or failing map never holds the screen longer than one
 * request should take; the published values then land as they arrive
 * (react-i18next re-renders on `added`). A switch to German, French or Italian
 * waits for that language's map with its bundle (the same budget), so the
 * switched screen shows published text at once; every switch asks again —
 * the browser revalidates by ETag, so an unchanged map costs a body-less 304
 * — and picks up a publish made meanwhile. English, the fallback, is fetched
 * at start too: its published values back every key a language lacks.
 * Never per screen.
 *
 * The map is asked for without credentials: it is the same for everyone, and
 * the sign-in page needs it before there is a session.
 *
 * Merging keeps the shipped bundle intact (`pristine`): each language's
 * resources are rebuilt as bundle + published values, so a value the platform
 * takes back falls back to the bundle text on the next load of the map.
 */

export const FIRST_PAINT_BUDGET_MS = 1500
// A switch right after a fetch of that language (the bundle read's own) asks
// no second time.
const REFETCH_AFTER_MS = 2000
const MISSING_REPORT_DELAY_MS = 5000
const MISSING_REPORT_BATCH = 50
const KEY_PATH = /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/

type PublishedValues = Record<string, string>

interface RuntimeMapBody {
  language: string
  version: number
  values: PublishedValues
}

const pristine = new Map<SupportedLanguage, ResourceKey>()
const published = new Map<SupportedLanguage, { version: number; values: PublishedValues }>()
const appliedVersion = new Map<SupportedLanguage, number>()
const inFlight = new Map<SupportedLanguage, Promise<void>>()
const fetchedAt = new Map<SupportedLanguage, number>()
let started: Promise<void> | null = null

function isSupported(language: string): language is SupportedLanguage {
  return (SUPPORTED_LANGUAGES as readonly string[]).includes(language)
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/** The bundle with the published values written over it (dotted paths). */
function merge(bundle: ResourceKey, values: PublishedValues): ResourceKey {
  const merged = clone(bundle) as Record<string, unknown>
  for (const [path, value] of Object.entries(values)) {
    const segments = path.split('.')
    let node = merged
    for (const segment of segments.slice(0, -1)) {
      const next = node[segment]
      if (next === null || typeof next !== 'object') node[segment] = {}
      node = node[segment] as Record<string, unknown>
    }
    node[segments[segments.length - 1]] = value
  }
  return merged as ResourceKey
}

function withBudget(work: Promise<void>): Promise<void> {
  return Promise.race([work, new Promise<void>((resolve) => setTimeout(resolve, FIRST_PAINT_BUDGET_MS))])
}

/** Writes bundle + published values into i18next for a language whose
 * bundle is in. A language not loaded yet is left alone: its bundle read
 * merges when it arrives (and a partial resource set would stop i18next from
 * ever fetching the bundle). */
function apply(language: SupportedLanguage): void {
  const current = published.get(language)
  if (!current || appliedVersion.get(language) === current.version) return
  if (!pristine.has(language)) {
    if (!i18n.hasResourceBundle(language, 'translation')) return
    pristine.set(language, clone(i18n.getResourceBundle(language, 'translation') as ResourceKey))
  }
  i18n.addResourceBundle(language, 'translation', merge(pristine.get(language)!, current.values), false, true)
  appliedVersion.set(language, current.version)
}

/** Fetches a language's published values (one at a time per language). A
 * failure keeps whatever was published before — the bundle, at worst. */
function fetchPublished(language: SupportedLanguage): Promise<void> {
  const running = inFlight.get(language)
  if (running) return running
  const work = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/i18n/${language}`, { credentials: 'omit' })
      if (!response.ok) return
      const body = (await response.json()) as RuntimeMapBody
      if (body && typeof body.version === 'number' && body.values && typeof body.values === 'object') {
        published.set(language, { version: body.version, values: body.values })
      }
    } catch {
      // Offline or refused: the bundle stays the answer.
    } finally {
      fetchedAt.set(language, Date.now())
      inFlight.delete(language)
    }
  })()
  const done = work.then(() => apply(language))
  inFlight.set(language, done)
  return done
}

/** The running fetch, nothing when one has just finished, else a new one. */
function fetchUnlessFresh(language: SupportedLanguage): Promise<void> {
  const running = inFlight.get(language)
  if (running) return running
  const last = fetchedAt.get(language)
  if (last !== undefined && Date.now() - last < REFETCH_AFTER_MS) return Promise.resolve()
  return fetchPublished(language)
}

function refresh(language: string): void {
  if (isSupported(language)) void fetchUnlessFresh(language)
}

// --- the missing-key report -----------------------------------------------------------------

const reported = new Set<string>()
const pending = new Map<string, Set<string>>()
let reportTimer: ReturnType<typeof setTimeout> | null = null

function queueMissing(key: string, language: string): void {
  if (!isSupported(language) || !KEY_PATH.test(key) || key.length > 255) return
  const id = `${language}\u0000${key}`
  if (reported.has(id)) return
  reported.add(id)
  if (!pending.has(language)) pending.set(language, new Set())
  pending.get(language)!.add(key)
  if (reportTimer === null) reportTimer = setTimeout(flushMissingReports, MISSING_REPORT_DELAY_MS)
}

/** Sends the keys queued since the last report — at most once per key and
 * language in a page's life, in batches the API accepts. A refused report
 * (no session yet, on the sign-in page) is dropped; the marker on screen
 * stays the loud part. Exported for tests. */
export async function flushMissingReports(): Promise<void> {
  if (reportTimer !== null) clearTimeout(reportTimer)
  reportTimer = null
  const batches: { language: string; keys: string[] }[] = []
  for (const [language, keys] of pending) {
    const all = [...keys]
    for (let i = 0; i < all.length; i += MISSING_REPORT_BATCH) {
      batches.push({ language, keys: all.slice(i, i + MISSING_REPORT_BATCH) })
    }
  }
  pending.clear()
  await Promise.all(
    batches.map((batch) =>
      fetch(`${API_BASE_URL}/i18n/missing-translations`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batch),
      }).catch(() => undefined),
    ),
  )
}

// --- start ----------------------------------------------------------------------------------

/**
 * Turns the runtime strings on (main.tsx, once). Resolves when the start
 * language's published values are in or the first-paint budget ran out —
 * never rejects. Later calls return the same promise.
 */
export function startRuntimeTranslations(): Promise<void> {
  if (started) return started
  runtimeTranslationHooks.afterBundleRead = async (language, bundle) => {
    pristine.set(language, clone(bundle))
    await withBudget(fetchUnlessFresh(language))
    const current = published.get(language)
    if (!current) return bundle
    appliedVersion.set(language, current.version)
    return merge(bundle, current.values)
  }
  runtimeTranslationHooks.onMissingKey = queueMissing
  i18n.on('languageChanged', refresh)

  // The start language's bundle read began when i18n initialised, before
  // these hooks existed, so its map is fetched here, beside it.
  const start = storedUiLanguage()
  if (start !== 'en') void fetchPublished('en')
  started = withBudget(fetchPublished(start))
  return started
}

/** After a publish in the Translations panel: this browser asks again for
 * every language it has loaded, so the editor sees the change at once
 * (everyone else sees it on their next load or switch). Nothing happens when
 * the runtime strings are off (a test that never started them). */
export async function reloadPublishedTranslations(): Promise<void> {
  if (!started) return
  const loaded = SUPPORTED_LANGUAGES.filter((language) => i18n.hasResourceBundle(language, 'translation'))
  await Promise.all(loaded.map((language) => (inFlight.get(language) ?? Promise.resolve()).then(() => fetchPublished(language))))
}

/** Test seam: forgets everything this module learnt and unhooks it. */
export function resetRuntimeTranslationsForTests(): void {
  for (const language of pristine.keys()) {
    if (i18n.hasResourceBundle(language, 'translation')) {
      i18n.addResourceBundle(language, 'translation', clone(pristine.get(language)!), false, true)
    }
  }
  pristine.clear()
  published.clear()
  appliedVersion.clear()
  inFlight.clear()
  fetchedAt.clear()
  reported.clear()
  pending.clear()
  if (reportTimer !== null) clearTimeout(reportTimer)
  reportTimer = null
  delete runtimeTranslationHooks.afterBundleRead
  delete runtimeTranslationHooks.onMissingKey
  i18n.off('languageChanged', refresh)
  started = null
}
