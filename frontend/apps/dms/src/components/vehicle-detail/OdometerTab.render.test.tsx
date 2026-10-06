// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { OdometerTab } from './OdometerTab'
import type { VehicleOdometerReadingRead } from '../../api/types'

// KAN-160 exit criterion 2: a kilometre cell renders through formatNumber
// — `12'500` under every UI language, fr-CH included, never the runtime's
// own CLDR grouping (U+2019 under ICU 77, a narrow space under fr-CH).

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const READING: VehicleOdometerReadingRead = {
  id: 'r1',
  value: 12500,
  readingDate: '2026-03-05',
  source: 'manual',
  implausible: false,
} as VehicleOdometerReadingRead

describe('OdometerTab', () => {
  it("renders a kilometre reading as 12'500 under the French UI", async () => {
    await i18n.changeLanguage('fr')
    render(
      <MantineProvider theme={theme} env="test">
        <OdometerTab readings={[READING]} loading={false} onAdd={vi.fn()} />
      </MantineProvider>,
    )
    expect(screen.getByText("12'500")).toBeInTheDocument()
  })
})
