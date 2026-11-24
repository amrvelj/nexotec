/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { basename, dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-464 PR-4 (ADR-210; Anto, 2026-10-26: one register, roles). Customers
// became Kontakte / Contacts / Contacts / Contatti in PR-3. This is the test
// that would have caught a half-rename: no translated string says «Kunde»,
// «customer», «client» or «cliente», and no SPA route is under /customers,
// except the entries below — each with the reason it stays. An entry that
// no longer matches anything fails too, so the list cannot go stale.
// The backend's half (API paths, schema fields, event names) is
// tests/architecture/test_kan464_no_customer_naming.py.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const LOCALES = join(SRC_ROOT, 'i18n', 'locales')
const LANGUAGES = ['de', 'en', 'fr', 'it'] as const
type Language = (typeof LANGUAGES)[number]

// The register's old name, per language. German: Kunde, Kunden, Kundin,
// Kundennummer, Stammkunde — not «Kundendienst» (after-sales service) and not
// «Sekundärfarbe». English: customer(s). French: client(e)(s), clientèle.
// Italian: cliente, clienti, clientela (not the loanword «client»).
const OLD_NAME: Record<Language, RegExp> = {
  de: /kund(e|in)(?!ndienst)/i,
  en: /\bcustomers?\b/i,
  fr: /\bclient(e|s|es|èle)?\b/i,
  it: /\bclient(e|i|ela)\b/i,
}

// `<namespace>:<key path>` → why it may use the word, and exactly the languages that do: the
// same key saying «Kunden» for the register in another language still fails.
const ALL: Language[] = ['de', 'en', 'fr', 'it']
const ALLOWED_STRINGS: Record<string, [string, Language[]]> = {
  // The role keeps its name: a contact holds the role «Kunde» (ADR-210).
  'contactEnums:role.customer': ['the role «Kunde» itself', ALL],
  'apiRefusal.reasons.contact:customer_role_required': ['names the role «Kunde»', ALL],
  'apiRefusal.reasons.contact:customer_role_not_permitted': ['names the role «Kunde»', ALL],
  'apiRefusal.reasons.contact:customer_role_credit_blocked': ['names the role «Kunde»', ALL],
  'contactDetail:mergeRefused.customerRoleNotPermitted': ['names the role «Kunde»', ALL],
  'contactDetail:overview.customerRoleFieldsHint': ['names the role «Kunde»', ALL],
  'contactDetail:overview.removeCustomerRole.title': ['names the role «Kunde»', ALL],
  'contactDetail:overview.removeCustomerRole.body': ['names the role «Kunde»', ALL],
  'contactRowMenu:removeDoNotContactCustomerRole': ['names the role «Kunde»', ALL],
  // AutoScout24's own credential field: the dealer's customer number at AutoScout24.
  'integrationEnums:configKey.kundennummer': ["the marketplace's account number, as it names it", ALL],
  // OAuth vocabulary («Client-ID», «client secret»), French uses the same word.
  'integrationEnums:configKey.clientId': ['OAuth client', ['fr']],
  'integrationEnums:secretSlot.client_secret': ['OAuth client', ['fr']],
  'integrationsList:connectModal.testAccessHint': ['OAuth client', ['fr']],
  'statusPage:platform.businessProfile.title': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.subtitle': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.loadError': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.cause.notConfigured': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.cause.refusal': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.outcome.answered': ["Nexotec's Google OAuth client", ['fr']],
  'statusPage:platform.businessProfile.outcome.refused': ["Nexotec's Google OAuth client", ['fr']],
}

// Source files that may still name the /customers route, and why.
const ALLOWED_ROUTE_FILES: Record<string, string> = {
  // Old links redirect to /contacts until KAN-464 PR-5 retires the old names.
  'App.tsx': 'the /customers → /contacts redirect',
}

type LocaleTree = { [key: string]: string | LocaleTree }

function flatten(tree: LocaleTree, prefix = ''): [string, string][] {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof value === 'string' ? [[path, value] as [string, string]] : flatten(value, path)
  })
}

function oldNameHits(): Map<string, Language[]> {
  const hits = new Map<string, Language[]>()
  for (const language of LANGUAGES) {
    const dir = join(LOCALES, language)
    for (const file of readdirSync(dir).filter((name) => name.endsWith('.json')).sort()) {
      const namespace = basename(file, '.json')
      const tree = JSON.parse(readFileSync(join(dir, file), 'utf8')) as LocaleTree
      for (const [key, value] of flatten(tree)) {
        if (!OLD_NAME[language].test(value)) continue
        const id = `${namespace}:${key}`
        hits.set(id, [...(hits.get(id) ?? []), language])
      }
    }
  }
  return hits
}

const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git', 'locales'])

function collectSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectSources(full, out)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && entry !== 'schema.d.ts') out.push(full)
  }
  return out
}

// A string or template literal holding the old route anywhere (`${base}/customers`), or a
// relative route `path="customers"`.
const OLD_ROUTE_LITERAL = /['"`][^'"`\n]*\/customers\b|path=["'{`]+customers\b/

describe('no half-rename: customers are contacts (KAN-464, ADR-210)', () => {
  it('no translated string uses the old name outside the allow-list', () => {
    const unexpected = [...oldNameHits().keys()].filter((id) => !(id in ALLOWED_STRINGS))
    expect(unexpected).toEqual([])
  })

  it('every allow-list entry matches in exactly its languages', () => {
    const hits = oldNameHits()
    const mismatched = Object.entries(ALLOWED_STRINGS)
      .filter(([id, [, languages]]) => (hits.get(id) ?? []).join() !== languages.join())
      .map(([id]) => `${id}: ${(hits.get(id) ?? []).join() || 'none'}`)
    expect(mismatched).toEqual([])
  })

  it('no route or link is under /customers outside the allow-list', () => {
    const files = collectSources(SRC_ROOT)
      .filter((file) => OLD_ROUTE_LITERAL.test(readFileSync(file, 'utf8')))
      .map((file) => relative(SRC_ROOT, file))
    expect(files.filter((file) => !(file in ALLOWED_ROUTE_FILES))).toEqual([])
    expect(Object.keys(ALLOWED_ROUTE_FILES).filter((file) => !files.includes(file))).toEqual([])
  })

  it('the patterns find the old name and spare the words that only look like it', () => {
    expect(OLD_NAME.de.test('Kunde')).toBe(true)
    expect(OLD_NAME.de.test('Neue Kunden')).toBe(true)
    expect(OLD_NAME.de.test('Stammkundin')).toBe(true)
    expect(OLD_NAME.de.test('Kundennummer')).toBe(true)
    expect(OLD_NAME.de.test('Verkauf und Kundendienst')).toBe(false)
    expect(OLD_NAME.de.test('Sekundärfarbe')).toBe(false)
    expect(OLD_NAME.en.test('New customer')).toBe(true)
    expect(OLD_NAME.en.test('Customs')).toBe(false)
    expect(OLD_NAME.fr.test('Nouveau client')).toBe(true)
    expect(OLD_NAME.fr.test('la clientèle')).toBe(true)
    expect(OLD_NAME.it.test('Nuovo cliente')).toBe(true)
    expect(OLD_NAME.it.test('Client-ID')).toBe(false)
    expect(OLD_ROUTE_LITERAL.test("navigate('/customers/' + id)")).toBe(true)
    expect(OLD_ROUTE_LITERAL.test('`/customers?role=x`')).toBe(true)
    expect(OLD_ROUTE_LITERAL.test('`${base}/customers`')).toBe(true)
    expect(OLD_ROUTE_LITERAL.test('<Route path="customers" />')).toBe(true)
    expect(OLD_ROUTE_LITERAL.test("navigate('/contacts')")).toBe(false)
  })
})
