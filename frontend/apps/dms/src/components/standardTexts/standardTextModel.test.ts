import { describe, expect, it } from 'vitest'
import de from '../../i18n/locales/de'
import en from '../../i18n/locales/en'
import fr from '../../i18n/locales/fr'
import it_ from '../../i18n/locales/it'
import { placeholderKey } from './standardTextModel'

// Every token the backend's closed set offers (app/platform/documents/services/
// standard_text_placeholders.py, `Placeholder`) has its label in all four
// bundles. KAN-464 PR-3: `customer.salutation` kept its name (Anto,
// 2026-11-24) when customers became contacts, so its label key keeps it too —
// a renamed key rendered the raw key on the «Platzhalter einfügen» button.
const TOKENS = [
  'dealership.name',
  'location.phone',
  'customer.salutation',
  'vehicle.make_model',
  'document.number',
  'document.date',
  'user.full_name',
  'payment.due_date',
]

function lookup(bundle: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], bundle)
}

describe('every standard-text placeholder has a label (KAN-464)', () => {
  it.each([
    ['de', de],
    ['fr', fr],
    ['it', it_],
    ['en', en],
  ])('%s', (_language, bundle) => {
    for (const token of TOKENS) {
      expect(typeof lookup(bundle as Record<string, unknown>, placeholderKey(token)), token).toBe('string')
    }
  })
})
