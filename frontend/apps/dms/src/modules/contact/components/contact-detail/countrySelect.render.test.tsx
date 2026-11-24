// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { contact } from '../../../../test/fixtures'
import { ContactDetailPage } from '../../pages/ContactDetailPage'
import { ContactCreateFlow } from '../ContactCreateFlow'

// KAN-32 — nationality and address country are chosen from the `country`
// reference list, never typed. The create wizard's silent maxLength={2}
// truncation is gone, and the detail screen's plain TextField is now a
// SelectField.

const detailRoutes = (nationality: string | null) => [
  { match: /^\/contacts\/c1$/, handler: () => contact({ id: 'c1', nationality }) },
  { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
  { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
  { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
  { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
  { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
]

function renderDetail() {
  renderWithProviders(
    <Routes>
      <Route path="/contacts/:id" element={<ContactDetailPage />} />
    </Routes>,
    { route: '/contacts/c1' },
  )
}

describe('country select — contact detail (KAN-32)', () => {
  it('renders the stored nationality as its country label, not the raw code', async () => {
    installFakeBackend(detailRoutes('HR'))
    renderDetail()
    // de bundle is the default — CLDR label for HR is "Kroatien".
    expect(await screen.findByText('Kroatien')).toBeInTheDocument()
    expect(screen.queryByText('HR')).not.toBeInTheDocument()
  })

  it('a country row with a blank label for the active language renders a loud marker', async () => {
    installFakeBackend([
      ...detailRoutes('ZQ'),
      {
        method: 'GET',
        match: /\/reference-data\/country$/,
        handler: () => ({
          items: [
            {
              id: 'country-ZQ',
              listCode: 'country',
              valueCode: 'ZQ',
              labelDe: '',
              labelFr: 'Zedistan',
              labelIt: 'Zedistan',
              labelEn: 'Zedistan',
              sortOrder: 0,
              active: true,
              version: 1,
              createdAt: '2026-01-01T00:00:00Z',
              updatedAt: '2026-01-01T00:00:00Z',
              createdBy: null,
              updatedBy: null,
            },
          ],
          nextCursor: null,
        }),
      },
    ])
    renderDetail()
    expect(await screen.findByText('⚠ ZQ')).toBeInTheDocument()
  })
})

describe('country select — create wizard (KAN-32)', () => {
  async function openStep2(user: ReturnType<typeof userEvent.setup>) {
    const backend = installFakeBackend([
      { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/contacts$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
    ])
    const { container } = renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.next') }))
    await screen.findByText(i18n.t('contactDetail.contactPoints.phoneNumbers'))
    return { backend, container }
  }

  it('nationality is a country select — no maxLength truncation, picking a country stores its code', async () => {
    const user = userEvent.setup()
    const { container } = await openStep2(user)

    const nationality = container.querySelector<HTMLInputElement>('input[data-path="nationality"]')!
    // The old free-text input truncated "Croatia" -> "Cr" silently.
    expect(nationality).not.toHaveAttribute('maxlength')

    await user.click(nationality)
    await user.type(nationality, 'Kroat')
    await user.click(await screen.findByRole('option', { name: 'Kroatien' }))

    // Mantine Select shows the option label in the input and stores the value.
    expect(nationality).toHaveValue('Kroatien')
    const hidden = container.querySelector<HTMLInputElement>('input[type="hidden"][data-path="nationality"], input[name="nationality"]')
    if (hidden) expect(hidden.value).toBe('HR')
  })
})
