// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { testTheme } from '../../test/testTheme'
import i18n from '../../i18n'
import { ApiError } from '../../api/client'
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
      <MantineProvider theme={testTheme} env="test">
        <OdometerTab readings={[READING]} loading={false} onAdd={vi.fn()} />
      </MantineProvider>,
    )
    expect(screen.getByText("12'500")).toBeInTheDocument()
  })

  // KAN-437: a refused reading is said in the UI language — here a NUL
  // character the API refuses (KAN-387), in Italian — never in its English.
  it('says a refused reading by its reason in Italian, never in the API\'s English text', async () => {
    await i18n.changeLanguage('it')
    const refused = new ApiError(422, 'unprocessable_entity', 'The request contains a character that is not allowed (NUL, U+0000).', {
      reason: 'nul_character',
      location: 'body',
    })
    render(
      <MantineProvider theme={testTheme} env="test">
        <OdometerTab readings={[]} loading={false} onAdd={() => Promise.reject(refused)} />
      </MantineProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: i18n.t('vehicleDetail.odometer.value') }), { target: { value: '12500' } })
    fireEvent.change(screen.getByLabelText(i18n.t('vehicleDetail.odometer.date')), { target: { value: '2026-10-26' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('vehicleDetail.odometer.record') }))

    expect(await screen.findByText(i18n.t('apiRefusal.reasons.nul_character'))).toBeInTheDocument()
    expect(screen.queryByText(/NUL, U\+0000/)).not.toBeInTheDocument()
  })
})
