// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { MappingGapPage } from '../api/types'
import { MappingGapsQueue } from './MappingGapsQueue'

// KAN-164 — "Last seen" used toLocaleDateString() with no locale, so it
// followed the browser (jsdom: en-US, `3/7/2026`) instead of the FR-13
// dd.MM.yyyy convention every other date in the app goes through formatDate.

// jsdom has no layout, so TanStack Virtual windows down to zero rows — mock
// it to "render every row" (same shim as VehiclesTab.render.test.tsx).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const page = (): MappingGapPage => ({
  items: [
    {
      id: 'gap-1',
      provider: 'auto_i_dat',
      vehicleKind: 'car',
      codeGroup: 'fuel',
      providerCode: 'XZ',
      occurrences: 12500,
      firstSeenAt: '2026-01-02T12:00:00Z',
      lastSeenAt: '2026-03-07T12:00:00Z',
      resolved: false,
      resolvedAt: null,
      resolvedValueCode: null,
    },
  ],
  nextCursor: null,
})

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('MappingGapsQueue — last-seen date follows FR-13 (KAN-164)', () => {
  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`renders dd.MM.yyyy in ${lng}`, async () => {
      await i18n.changeLanguage(lng)
      installFakeBackend([{ match: /^\/vehicle-mdm\/mapping-gaps$/, handler: () => page() }])
      renderWithProviders(<MappingGapsQueue />)
      expect(await screen.findByText('07.03.2026')).toBeInTheDocument()
      expect(screen.queryByText('3/7/2026')).not.toBeInTheDocument()
    })
  }
})
