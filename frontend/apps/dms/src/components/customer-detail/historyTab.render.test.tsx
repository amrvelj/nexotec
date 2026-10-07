// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import type { AuditEventRead } from '../../api/types'
import { HistoryTab } from './HistoryTab'

// KAN-164 — the History tab rendered every before/after value with
// String(v): a credit limit of 12500 read `12500.00`, a merge count `12500`,
// and booleans were a hardcoded English `Yes` / `No`. Values are now
// formatted by field: amounts as CHF, counts Swiss-grouped, identifiers
// (customer number, postal code) left exactly as recorded.

function event(over: Partial<AuditEventRead>): AuditEventRead {
  return {
    id: 'ev-1',
    entityType: 'customer',
    entityId: 'c1',
    tenantId: 'group-1',
    action: 'update',
    actorId: null,
    before: null,
    after: null,
    reason: null,
    createdAt: '2026-10-01T10:00:00Z',
    ...over,
  }
}

function renderHistory(events: AuditEventRead[]) {
  installFakeBackend([])
  return renderWithProviders(<HistoryTab events={events} loading={false} error={null} locale="de-CH" />)
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('HistoryTab — values are formatted by field (KAN-164)', () => {
  it('renders the credit limit as a CHF amount', () => {
    renderHistory([event({ before: { credit_limit: '9000.00' }, after: { credit_limit: '12500.00' } })])
    expect(screen.getByText(/CHF 9'000\.00 → CHF 12'500\.00/)).toBeInTheDocument()
  })

  it('groups merge counts and leaves identifiers ungrouped', () => {
    renderHistory([
      event({
        action: 'merge',
        before: { customer_number: 'K-012500', address_postal_code: '80000' },
        after: { customer_number: '12500', address_postal_code: '80001', tagsRepointed: 12500 },
      }),
    ])
    expect(screen.getByText(/— → 12'500/)).toBeInTheDocument()
    expect(screen.getByText(/K-012500 → 12500$/)).toBeInTheDocument()
    expect(screen.getByText(/80000 → 80001/)).toBeInTheDocument()
  })

  // KAN-160 split this out because "a number there may be a year": a
  // number under a key that is not an amount or a count stays ungrouped,
  // and so does any key of an entity other than the customer.
  it('leaves numbers under other keys and other entities ungrouped', () => {
    renderHistory([
      event({ id: 'ev-1', before: { model_year: 2019 }, after: { model_year: 2024 } }),
      event({ id: 'ev-2', entityType: 'vehicle', before: { credit_limit: 9000 }, after: { credit_limit: 12500 } }),
    ])
    expect(screen.getByText(/2019 → 2024/)).toBeInTheDocument()
    expect(screen.getByText(/9000 → 12500/)).toBeInTheDocument()
  })

  it('leaves a redacted or non-numeric amount as recorded', () => {
    renderHistory([event({ before: { credit_limit: null }, after: { credit_limit: '***redacted***' } })])
    expect(screen.getByText(/— → \*\*\*redacted\*\*\*/)).toBeInTheDocument()
  })

  // Hardcoded, not derived via i18n.t(): the expectation must not be
  // computed the way the component computes it.
  const YES_NO: Record<'de' | 'fr' | 'it' | 'en', [string, string]> = {
    de: ['Nein', 'Ja'],
    fr: ['Non', 'Oui'],
    it: ['No', 'Sì'],
    en: ['No', 'Yes'],
  }
  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`translates booleans in ${lng}`, async () => {
      await i18n.changeLanguage(lng)
      renderHistory([event({ before: { newsletter: false }, after: { newsletter: true } })])
      const [no, yes] = YES_NO[lng]
      expect(screen.getByText(new RegExp(`${no} → ${yes}`))).toBeInTheDocument()
    })
  }
})
