// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
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

  // KAN-177: the merge now reports the kept-and-closed vehicle-party rows as a
  // count (grouped like the other counts) and their ids (not a number, left as is).
  it('groups the kept-and-closed vehicle-party count of a merge', () => {
    renderHistory([
      event({
        action: 'merge',
        before: null,
        after: { vehiclePartiesKeptClosed: 1500, vehiclePartiesKeptClosedIds: ['01a1'] },
      }),
    ])
    expect(screen.getByText(/— → 1'500/)).toBeInTheDocument()
    expect(screen.getByText(/\["01a1"\]/)).toBeInTheDocument()
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

// KAN-180 — a create event's `before` is null, so every audited field was
// `undefined` before and `null` after: the two stringify differently and
// each empty field was listed as `Customer since: — → —`, about 25 noise
// rows per customer. And the backend's `_plain` writes a date as its ISO
// string (`1980-05-01`), which rendered raw instead of dd.MM.yyyy (FR-13).
describe('HistoryTab — empty fields are not listed and dates are formatted (KAN-180)', () => {
  // A zone west of Greenwich with no daylight saving: `new Date('1980-05-01')`
  // is UTC midnight, which is still 30 April here. A date-only value names
  // a calendar day, and must render as that day wherever the user sits.
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('a create event lists only the fields that were set', () => {
    renderHistory([
      event({
        action: 'create',
        before: null,
        after: { first_name: 'Hans', customer_since: null, next_follow_up: null, website: null, tags: [] },
      }),
    ])
    expect(screen.getByText(/— → Hans/)).toBeInTheDocument()
    expect(screen.queryByText(/— → —/)).not.toBeInTheDocument()
    // The field names are the German UI labels since KAN-336 (the tests run in DE).
    expect(screen.queryByText('Kunde seit')).not.toBeInTheDocument()
    expect(screen.queryByText('Nächster Kontakt')).not.toBeInTheDocument()
    expect(screen.queryByText('Website')).not.toBeInTheDocument()
  })

  // KAN-348 — an empty collection is an empty field too: a create event's
  // `after` carries `tags: []`, which read `Tags: — → []`.
  it('a create event does not list an empty collection', () => {
    renderHistory([event({ action: 'create', before: null, after: { first_name: 'Hans', tags: [] } })])
    expect(screen.getByText(/— → Hans/)).toBeInTheDocument()
    expect(screen.queryByText('Tags')).not.toBeInTheDocument()
    expect(screen.queryByText(/\[\]/)).not.toBeInTheDocument()
  })

  it('a collection filled or emptied is still listed', () => {
    renderHistory([
      event({ before: { tags: [] }, after: { tags: ['vip'] } }),
      event({ id: 'ev-2', before: { tags: ['vip'] }, after: { tags: [] } }),
    ])
    expect(screen.getByText(/— → \["vip"\]/)).toBeInTheDocument()
    expect(screen.getByText(/\["vip"\] → —/)).toBeInTheDocument()
  })

  it('a field cleared to empty is still listed', () => {
    renderHistory([event({ before: { website: 'https://muster.ch', next_follow_up: '2026-03-05' }, after: { website: null, next_follow_up: null } })])
    expect(screen.getByText(/https:\/\/muster\.ch → —/)).toBeInTheDocument()
    expect(screen.getByText(/05\.03\.2026 → —/)).toBeInTheDocument()
  })

  it('date fields render dd.MM.yyyy on the recorded day, whatever the time zone', () => {
    renderHistory([
      event({
        before: { birth_date: '1980-05-01' },
        after: { birth_date: '1980-05-02', customer_since: '2026-01-31', next_follow_up: '2026-12-31' },
      }),
    ])
    expect(screen.getByText(/01\.05\.1980 → 02\.05\.1980/)).toBeInTheDocument()
    expect(screen.getByText(/— → 31\.01\.2026/)).toBeInTheDocument()
    expect(screen.getByText(/— → 31\.12\.2026/)).toBeInTheDocument()
    expect(screen.queryByText(/1980-05-0/)).not.toBeInTheDocument()
    expect(screen.queryByText(/30\.04\.1980/)).not.toBeInTheDocument()
  })

  it('timestamps render as date and time in the local zone', () => {
    renderHistory([
      event({
        action: 'phone_update',
        before: { consentTimestamp: null },
        after: { consentTimestamp: '2026-10-01T10:00:00+00:00' },
      }),
      event({
        id: 'ev-2',
        action: 'vehicle_party_update',
        before: { effectiveFrom: '2026-03-05T12:00:00+00:00', effectiveTo: null },
        after: { effectiveFrom: '2026-03-05T12:00:00+00:00', effectiveTo: '2026-03-06T13:30:00+00:00' },
      }),
    ])
    // 10:00 UTC is 00:00 in Honolulu (UTC−10).
    expect(screen.getByText(/— → 01\.10\.2026, 00:00/)).toBeInTheDocument()
    expect(screen.getByText(/— → 06\.03\.2026, 03:30/)).toBeInTheDocument()
    expect(screen.queryByText(/2026-10-01T/)).not.toBeInTheDocument()
    expect(screen.queryByText(/2026-03-06T/)).not.toBeInTheDocument()
    // effectiveFrom did not change: not listed.
    expect(screen.queryByText('Seit')).not.toBeInTheDocument()
  })

  it('a date field holding something other than a date is shown as recorded', () => {
    renderHistory([event({ before: { birth_date: null }, after: { birth_date: '***redacted***' } })])
    expect(screen.getByText(/— → \*\*\*redacted\*\*\*/)).toBeInTheDocument()
  })
})
