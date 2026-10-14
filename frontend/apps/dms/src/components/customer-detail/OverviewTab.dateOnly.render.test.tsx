// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { customer } from '../../test/fixtures'
import { OverviewTab } from './OverviewTab'

// KAN-333 — birth date, customer since and next follow-up are date-only
// values (`1980-05-01`). formatDate parsed them as UTC midnight and rendered
// the day before anywhere west of Greenwich; formatDateOnly keeps the day.
describe('OverviewTab — date-only fields keep their day west of Greenwich (KAN-333)', () => {
  // UTC−10, no daylight saving: UTC midnight is still the previous day here.
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('renders birth date, customer since and next follow-up as the recorded day', async () => {
    installFakeBackend([{ match: /^\/customers\/advisor-options$/, handler: () => ({ items: [] }) }])
    const save = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(
      <OverviewTab
        customer={customer({ birthDate: '1980-05-01', customerSince: '2020-01-15', nextFollowUp: '2026-11-01' })}
        phones={[]}
        emails={[]}
        onSaveField={save}
        isConflict={() => false}
        onReload={vi.fn()}
        onSaveAddress={save}
        onCreatePhone={save}
        onUpdatePhone={save}
        onDeletePhone={save}
        onCreateEmail={save}
        onUpdateEmail={save}
        onDeleteEmail={save}
        countryOptions={[]}
        locale="de-CH"
      />,
    )

    expect(await screen.findByText('01.05.1980')).toBeInTheDocument()
    expect(screen.getByText('15.01.2020')).toBeInTheDocument()
    expect(screen.getByText('01.11.2026')).toBeInTheDocument()
    expect(screen.queryByText('30.04.1980')).not.toBeInTheDocument()
    expect(screen.queryByText('14.01.2020')).not.toBeInTheDocument()
    expect(screen.queryByText('31.10.2026')).not.toBeInTheDocument()
  })
})
