// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen } from '@testing-library/react'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { contact } from '../../../../test/fixtures'
import { ContactDetailPage } from '../../pages/ContactDetailPage'

// KAN-469 — external IDs are written by Nexotec (platform staff) and by
// connected OEM/CRM systems, never by the dealer (PRD-Contacts FR-08; Anto,
// 2026-08-07, re-confirmed 2026-10-26). A dealer saw an empty tab with no add
// action and read it as a missing feature: the tab now says who maintains
// these links, in every UI language.

afterEach(async () => {
  await i18n.changeLanguage('de')
})

const STAFF = {
  id: 'staff-1',
  dealershipId: 'dealership-1',
  firstName: 'Nora',
  lastName: 'Nexotec',
  email: 'nora@nexotec.example',
  phone: null,
  role: 'other',
  accessRoles: ['platform_admin'],
  isDealerManager: false,
  employmentStatus: 'active',
  authIdentityId: 'auth-staff',
  status: 'active',
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

function installBackend({ staff = false } = {}) {
  const record = contact({ id: 'c1', contactNumber: 'K-1001' })
  const me = staff
    ? [
        {
          method: 'GET',
          match: /\/auth\/me$/,
          handler: () => ({
            user: STAFF,
            activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
            memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
            entitlements: ['sales'],
          }),
        },
      ]
    : []
  return installFakeBackend([
    ...me,
    { match: /^\/contacts\/c1$/, handler: () => record },
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
  ])
}

describe('External IDs tab — who maintains the links (KAN-469)', () => {
  for (const language of ['de', 'fr', 'it', 'en'] as const) {
    it(`tells a dealer user in ${language} that Nexotec and connected systems maintain them, and offers no add action`, async () => {
      await i18n.changeLanguage(language)
      installBackend()
      renderWithProviders(
        <Routes>
          <Route path="/contacts/:id" element={<ContactDetailPage />} />
        </Routes>,
        { route: '/contacts/c1?tab=external-ids' },
      )

      expect(await screen.findByText(i18n.t('contactDetail.externalIds.maintainedBy'))).toBeInTheDocument()
      expect(await screen.findByText(i18n.t('contactDetail.externalIds.emptyState.description'))).toBeInTheDocument()
      expect(screen.queryByText(/MISSING I18N KEY/)).not.toBeInTheDocument()
      // The default signed-in user is a sales user, not platform staff: read only.
      expect(screen.queryByRole('button', { name: i18n.t('contactDetail.externalIds.add') })).not.toBeInTheDocument()
    })
  }

  it('gives platform staff, who maintain the links, the add action instead of the reader\'s explanation', async () => {
    installBackend({ staff: true })
    renderWithProviders(
      <Routes>
        <Route path="/contacts/:id" element={<ContactDetailPage />} />
      </Routes>,
      { route: '/contacts/c1?tab=external-ids' },
    )

    expect(await screen.findByRole('button', { name: i18n.t('contactDetail.externalIds.add') })).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('contactDetail.externalIds.maintainedBy'))).not.toBeInTheDocument()
  })
})
