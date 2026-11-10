/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { afterEach, describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import i18n from '../i18n'
import de from '../i18n/locales/de'
import en from '../i18n/locales/en'
import fr from '../i18n/locales/fr'
import it_ from '../i18n/locales/it'
import { ApiError } from '../api/client'
import { apiRefusalText, isStaleVersion, refusalReasonKey, TRANSLATED_REFUSAL_REASONS } from './apiRefusal'

// KAN-437 — the one helper every converted screen says an API refusal with.

const t = i18n.t.bind(i18n)
const FALLBACK = 'stockDetail.purchase.error'
const ENGLISH = 'The API said this in English.'

const refusal = (status: number, details: Record<string, unknown> | null = null, code = 'conflict') =>
  new ApiError(status, code, ENGLISH, details)

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('apiRefusalText — the API refusal in the UI language, never its English message', () => {
  it('says a known reason by its key, in the active language', async () => {
    await i18n.changeLanguage('fr')
    expect(apiRefusalText(t, refusal(409, { reason: 'no_signed_contract' }), FALLBACK)).toBe(fr.apiRefusal.reasons.no_signed_contract)
    await i18n.changeLanguage('it')
    expect(apiRefusalText(t, refusal(400, { reason: 'last_manager' }, 'bad_request'), FALLBACK)).toBe(it_.usersAdmin.errors.lastManager)
  })

  it('names the value a reason carries', async () => {
    await i18n.changeLanguage('fr')
    const text = apiRefusalText(t, refusal(409, { reason: 'vin_already_in_stock', stockNumber: 'S-000042' }), FALLBACK)
    expect(text).toBe(fr.apiRefusal.reasons.vin_already_in_stock.replace('{{stockNumber}}', 'S-000042'))
  })

  // KAN-509: one configuration, one stock car.
  it('names the stock car that already holds the configuration', async () => {
    await i18n.changeLanguage('it')
    const text = apiRefusalText(t, refusal(409, { reason: 'configuration_held_by_stock_item', stockItemId: 'si-1', stockNumber: 'S-000007' }), FALLBACK)
    expect(text).toBe(it_.apiRefusal.reasons.configuration_held_by_stock_item.replace('{{stockNumber}}', 'S-000007'))
  })

  // KAN-472: the reasons whose sentence names what the refusal carries.
  it('names the containers an offer still misses, by their names in the UI language', async () => {
    await i18n.changeLanguage('it')
    const text = apiRefusalText(t, refusal(409, { reason: 'missing_containers', missingContainers: ['customer', 'trade_in'] }), FALLBACK)
    expect(text).toBe(it_.apiRefusal.reasons.missing_containers.replace('{{containers}}', `${it_.offerWorkspace.containers.customer}, ${it_.offerWorkspace.containers.tradeIn}`))
  })

  it('names the do-not-contact customer, the mapping in place, the image limit and the missing settings', async () => {
    await i18n.changeLanguage('fr')
    const r = fr.apiRefusal.reasons
    expect(apiRefusalText(t, refusal(409, { reason: 'customer_do_not_contact', customerNumber: 'K-1001' }), FALLBACK)).toBe(
      r.customer_do_not_contact.replace('{{customerNumber}}', 'K-1001'),
    )
    expect(
      apiRefusalText(t, refusal(409, { reason: 'provider_code_already_mapped', canonicalListCode: 'fuel_type', canonicalValueCode: 'petrol' }), FALLBACK),
    ).toBe(r.provider_code_already_mapped.replace('{{mapping}}', 'fuel_type/petrol'))
    expect(apiRefusalText(t, refusal(409, { reason: 'too_many_images', maxImages: 16 }), FALLBACK)).toBe(r.too_many_images.replace('{{max, number}}', '16'))
    expect(
      apiRefusalText(t, refusal(422, { reason: 'missing_config_fields', missingConfigKeys: ['username', 'notAKnownKey'] }, 'unprocessable_entity'), FALLBACK),
    ).toBe(r.missing_config_fields.replace('{{fields}}', `${fr.integrationEnums.configKey.username}, notAKnownKey`))
  })

  it("names the access window's days in the user's locale, as calendar days", async () => {
    await i18n.changeLanguage('de')
    const closed = (details: Record<string, unknown>) =>
      apiRefusalText(t, refusal(403, { reason: 'access_window_closed', ...details }, 'forbidden'), FALLBACK)
    const sentence = (window: string) => de.apiRefusal.reasons.access_window_closed.replace('{{window}}', window)
    expect(closed({ validFrom: '2026-11-01', validTo: null })).toBe(sentence(de.apiRefusal.accessWindow.from.replace('{{from}}', '01.11.2026')))
    expect(closed({ validFrom: null, validTo: '2026-10-01' })).toBe(sentence(de.apiRefusal.accessWindow.until.replace('{{to}}', '01.10.2026')))
    expect(closed({ validFrom: '2026-11-01', validTo: '2026-12-31' })).toBe(
      sentence(de.apiRefusal.accessWindow.between.replace('{{from}}', '01.11.2026').replace('{{to}}', '31.12.2026')),
    )
    expect(closed({})).toBe(sentence(de.apiRefusal.accessWindow.none))
    expect(closed({ validFrom: 'not a day', validTo: 42 })).toBe(sentence(de.apiRefusal.accessWindow.none))
    expect(closed({ validFrom: '2026-13-45' })).toBe(sentence(de.apiRefusal.accessWindow.none))
  })

  it('says a stale If-Match 409 as such, before any reason', () => {
    expect(apiRefusalText(t, refusal(409, { currentVersion: 4, reason: 'contract_not_pending' }), FALLBACK)).toBe(de.apiRefusal.staleVersion)
  })

  it('says a 403, a 404 and a schema 422 without a reason by their status', async () => {
    await i18n.changeLanguage('it')
    expect(apiRefusalText(t, refusal(403, null, 'forbidden'), FALLBACK)).toBe(it_.apiRefusal.forbidden)
    expect(apiRefusalText(t, refusal(404, null, 'not_found'), FALLBACK)).toBe(it_.apiRefusal.notFound)
    expect(apiRefusalText(t, refusal(422, { errors: [{ loc: ['body', 'x'] }] }, 'unprocessable_entity'), FALLBACK)).toBe(it_.apiRefusal.invalid)
  })

  // The review of KAN-437: a business rule refused without a reason is a
  // 400 or 422 too (the last usable phone, a closed accounting period, a
  // passkey that does not verify) — never "check your entries", always the
  // screen's own wording for what failed.
  it("says a business rule's 400 or 422 without a reason with the screen's own key", async () => {
    await i18n.changeLanguage('it')
    expect(apiRefusalText(t, refusal(400, null, 'bad_request'), FALLBACK)).toBe(it_.stockDetail.purchase.error)
    expect(apiRefusalText(t, refusal(422, null, 'unprocessable_entity'), FALLBACK)).toBe(it_.stockDetail.purchase.error)
    expect(apiRefusalText(t, refusal(422, { location: 'body' }, 'unprocessable_entity'), FALLBACK)).toBe(it_.stockDetail.purchase.error)
  })

  it("falls back to the screen's own key for an unknown reason, any other status and an error that is not the API's", () => {
    expect(apiRefusalText(t, refusal(409, { reason: 'not_a_reason_the_bundles_know' }), FALLBACK)).toBe(de.stockDetail.purchase.error)
    expect(apiRefusalText(t, refusal(503, null, 'unavailable'), FALLBACK)).toBe(de.stockDetail.purchase.error)
    expect(apiRefusalText(t, new Error(ENGLISH), FALLBACK)).toBe(de.stockDetail.purchase.error)
    expect(apiRefusalText(t, 'boom', FALLBACK)).toBe(de.stockDetail.purchase.error)
  })

  it("says a response without the API's error body as a failed request (KAN-271)", () => {
    expect(apiRefusalText(t, refusal(504, null, 'unknown_error'), FALLBACK)).toBe(de.common.requestFailed)
  })

  it("never returns the API's message, whatever the refusal", () => {
    for (const status of [400, 401, 403, 404, 409, 422, 500, 503]) {
      for (const details of [null, { reason: 'x' }, { reason: 'credit_block' }, { currentVersion: 1 }]) {
        expect(apiRefusalText(t, refusal(status, details), FALLBACK)).not.toContain(ENGLISH)
      }
    }
  })
})

// KAN-473: only a stale If-Match 409 offers a reload; a business rule's 409
// (a phone already on the customer) is not fixed by one.
describe('isStaleVersion', () => {
  it('is a 409 naming the current version, and nothing else', () => {
    expect(isStaleVersion(refusal(409, { currentVersion: 4, ifMatchVersion: 3 }))).toBe(true)
    expect(isStaleVersion(refusal(409, { reason: 'phone_already_on_customer' }))).toBe(false)
    expect(isStaleVersion(refusal(409, null))).toBe(false)
    expect(isStaleVersion(refusal(400, { currentVersion: 4 }))).toBe(false)
    expect(isStaleVersion(new Error('409'))).toBe(false)
  })
})

describe('the reason table', () => {
  const bundles: Record<string, Record<string, unknown>> = { de, en, fr, it: it_ }
  const lookup = (bundle: Record<string, unknown>, key: string): unknown =>
    key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], bundle)

  it('names a key that exists in all four bundles for every reason', () => {
    const missing: string[] = []
    for (const reason of TRANSLATED_REFUSAL_REASONS) {
      const key = refusalReasonKey(reason)!
      for (const [lang, bundle] of Object.entries(bundles)) {
        if (typeof lookup(bundle, key) !== 'string') missing.push(`${lang}: ${reason} → ${key}`)
      }
    }
    expect(missing).toEqual([])
  })

  it('knows no reason by inheritance (toString, constructor)', () => {
    expect(refusalReasonKey('toString')).toBeNull()
    expect(refusalReasonKey('constructor')).toBeNull()
  })

  // A reason the API starts raising without a sentence here would still read
  // as the screen's translated generic — never English — but the user would
  // lose what went wrong. The scan reads every literal `"reason": "…"` the
  // backend puts in a refusal's details (and the module constants passed as
  // one), so a new reason ships with its four sentences. The same literal
  // also names why a session ended or a login failed in an auth event's
  // details, and why a contract confirmation was logged — never a refusal
  // body, so never on screen; those are listed here by name.
  const NOT_A_REFUSAL = new Set([
    'confirmed_concurrently', // sales/services/contract.py: a log line's extra
    'factor_enrolled', // platform/session_guard.py: session_revoked event
    'inactive', // platform/services/identity.py: login_failed event (the 401 is uniform)
    'logout', // platform/api/auth.py: session_revoked event
    'mfa_removed', // platform/api/auth.py: session_revoked event
    'no_credential', // platform/services/identity.py: login_failed event
    'password', // platform/services/identity.py: login_failed event
    'user', // platform/api/auth.py: session_revoked event
  ])
  it('translates every literal reason the API raises', () => {
    const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', '..', 'app')
    const raised = new Set<string>()
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry)
        if (statSync(full).isDirectory()) walk(full)
        else if (entry.endsWith('.py')) {
          const source = readFileSync(full, 'utf-8')
          for (const m of source.matchAll(/"reason":\s*"([a-z0-9_]+)"/g)) raised.add(m[1])
          for (const m of source.matchAll(/^(?:REFUSAL_REASON|COLLEAGUE_CANNOT_ACT_THERE)\s*=\s*"([a-z0-9_]+)"/gm)) raised.add(m[1])
          for (const m of source.matchAll(/reason: str = "([a-z0-9_]+)"/g)) raised.add(m[1])
        }
      }
    }
    walk(appRoot)
    // The scan read the backend: these are raised today.
    expect(raised).toContain('no_signed_contract')
    expect(raised).toContain('plate_read_limit_reached')
    expect(raised).toContain('last_manager')
    for (const reason of NOT_A_REFUSAL) expect(raised, `${reason} is no longer raised — drop it from the list`).toContain(reason)
    expect([...raised].filter((reason) => !NOT_A_REFUSAL.has(reason) && refusalReasonKey(reason) === null).sort()).toEqual([])
  })
})

// The blocking-condition banner says the API's German-only sentence from its
// marketplace field (KAN-437); a field the API starts naming without a
// sentence there would read as the generic one. The scan keeps the two lists
// together.
describe('the blocking-condition banner knows every field the API names', () => {
  it('has a sentence for each field publishing.py names a condition by', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const publishing = readFileSync(join(here, '..', '..', '..', '..', '..', 'app', 'inventory', 'services', 'publishing.py'), 'utf-8')
    const banner = readFileSync(join(here, '..', 'components', 'stock-detail', 'BlockingConditionBanner.tsx'), 'utf-8')
    const named = [...publishing.matchAll(/BlockingCondition\(\s*field="([^"]+)"/g)].map((m) => m[1])
    const known = banner.match(/const BLOCKING_FIELDS = new Set\(\[([^\]]*)\]\)/)?.[1] ?? ''
    expect(named.length).toBeGreaterThan(0)
    expect(named.filter((field) => !known.includes(`'${field}'`))).toEqual([])
    for (const field of named) {
      for (const [lang, bundle] of Object.entries({ de, en, fr, it: it_ })) {
        const sentence = (bundle.stockDetail.publishing.blockingCondition as Record<string, string>)[field]
        expect(typeof sentence, `${lang}: ${field}`).toBe('string')
      }
    }
  })
})
