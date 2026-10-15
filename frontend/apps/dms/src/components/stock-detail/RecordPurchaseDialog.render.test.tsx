// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen } from '@testing-library/react'
import i18n from '../../i18n'
import { installFakeBackend } from '../../test/fakeBackend'
import { renderWithProviders } from '../../test/renderWithProviders'
import { RecordPurchaseDialog } from './RecordPurchaseDialog'

// KAN-358 — the purchase date drives the Wagenbuch and the fiktiver
// Vorsteuerabzug booking date. It was pre-filled with the UTC day, so a
// purchase recorded at 00:30 Swiss time proposed yesterday.

const ORIGINAL_TZ = process.env.TZ

beforeEach(() => {
  process.env.TZ = 'Europe/Zurich'
  vi.useFakeTimers({ toFake: ['Date'] })
  installFakeBackend([])
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  process.env.TZ = ORIGINAL_TZ
})

function purchaseDateField(): HTMLInputElement {
  return screen.getByLabelText(i18n.t('stockDetail.purchase.fields.purchaseDate'), { exact: false }) as HTMLInputElement
}

describe('RecordPurchaseDialog — the default purchase date (KAN-358)', () => {
  it('is the Swiss calendar day at 00:30 in summer, not the UTC day before', () => {
    // 2026-10-14T22:30Z is 00:30 on 15 October in Zurich (CEST, UTC+2).
    vi.setSystemTime(new Date('2026-10-14T22:30:00Z'))
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={async () => {}} />)

    expect(purchaseDateField().value).toBe('2026-10-15')
  })

  it('is the Swiss calendar day at 00:30 in winter', () => {
    // 2026-12-31T23:30Z is 00:30 on 1 January 2027 in Zurich (CET, UTC+1).
    vi.setSystemTime(new Date('2026-12-31T23:30:00Z'))
    renderWithProviders(<RecordPurchaseDialog opened onClose={() => {}} onSubmit={async () => {}} />)

    expect(purchaseDateField().value).toBe('2027-01-01')
  })
})
