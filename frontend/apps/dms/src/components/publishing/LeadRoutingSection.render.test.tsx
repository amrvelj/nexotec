// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/renderWithProviders'
import { installFakeBackend } from '../../test/fakeBackend'
import { ApiError } from '../../api/client'
import type { LeadRoutingRead, PublishingProfileRead } from '../../api/types'
import { LeadRoutingSection } from './LeadRoutingSection'

// KAN-734 (Anto, 2026-11-15, KAN-225 Q3 A): a location's lead e-mail is the
// manager's on both screens. On Publishing the inventory role still edits the
// rest of the lead routing; the e-mail is read-only for it, with the reason.

const routing: LeadRoutingRead = {
  locationId: 'loc-1',
  locationName: 'Zürich Nord',
  leadEmail: 'leads@garage-muster.ch',
  leadPhone: null,
  createsTask: false,
  recipientUserId: null,
  recipientRole: null,
}
const profile = { dealershipId: 'dealership-1', version: 4, leadRouting: [routing] } as unknown as PublishingProfileRead

const emailField = () => screen.getByRole('textbox', { name: (n) => n.startsWith('E-Mail für Anfragen') }) as HTMLInputElement
const phoneField = () => screen.getByRole('textbox', { name: (n) => n.startsWith('Telefon für Anfragen') }) as HTMLInputElement

describe('LeadRoutingSection — the lead e-mail is the manager\'s (KAN-734)', () => {
  it('shows the e-mail read-only to a role that may not write Locations, with the reason, and saves the rest', async () => {
    const backend = installFakeBackend([
      { method: 'PUT', match: /\/lead-routing$/, handler: () => ({ ...profile, version: 5 }) },
    ])
    const user = userEvent.setup()
    renderWithProviders(
      <LeadRoutingSection profile={profile} routing={routing} canWrite canWriteLeadEmail={false} onWritten={() => {}} />,
    )

    expect(emailField()).toHaveAttribute('readonly')
    expect(emailField().value).toBe('leads@garage-muster.ch')
    expect(screen.getByText('Nur die Geschäftsführung ändert die E-Mail für Anfragen – hier und unter Standorte.')).toBeInTheDocument()
    expect(phoneField()).not.toHaveAttribute('readonly')

    await user.type(phoneField(), '+41 44 000 00 00')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/lead-routing$/, 'PUT')).toHaveLength(1))
    // The e-mail goes back as it was: no change to it, so the server accepts the rest.
    expect(backend.callsTo(/\/lead-routing$/, 'PUT')[0].body).toMatchObject({ leadEmail: 'leads@garage-muster.ch', leadPhone: '+41 44 000 00 00' })
  })

  it('lets the manager edit the e-mail, with no restriction hint', () => {
    installFakeBackend([])
    renderWithProviders(<LeadRoutingSection profile={profile} routing={routing} canWrite canWriteLeadEmail onWritten={() => {}} />)

    expect(emailField()).not.toHaveAttribute('readonly')
    expect(screen.queryByText(/Nur die Geschäftsführung ändert/)).not.toBeInTheDocument()
  })

  it('says the server\'s refusal in the UI language', async () => {
    const refused = new ApiError(403, 'forbidden', "Only the dealer manager may change a location's lead e-mail.", {
      reason: 'lead_email_manager_only',
      field: 'leadEmail',
    })
    installFakeBackend([{ method: 'PUT', match: /\/lead-routing$/, handler: () => { throw refused } }])
    const user = userEvent.setup()
    renderWithProviders(<LeadRoutingSection profile={profile} routing={routing} canWrite canWriteLeadEmail onWritten={() => {}} />)

    await user.clear(emailField())
    await user.type(emailField(), 'neu@garage-muster.ch')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('Die E-Mail für Anfragen ändert nur die Geschäftsführung.')).toBeInTheDocument()
    expect(screen.queryByText(/Only the dealer manager/)).not.toBeInTheDocument()
  })
})
