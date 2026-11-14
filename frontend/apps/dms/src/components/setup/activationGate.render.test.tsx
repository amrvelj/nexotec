// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import i18n from '../../i18n'
import { renderWithProviders } from '../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../../test/fakeBackend'
import { ProtectedRoute } from '../ProtectedRoute'
import { LegalAcceptancePage } from '../../pages/LegalAcceptancePage'

// KAN-214 — the SPA side of the activation gate and the legal hold
// (presentation only; the server is the boundary): while the Betrieb is being
// set up the manager lands on the checklist and everyone else on «Betrieb
// wird eingerichtet»; a new ToS / DPA version prompts the manager at login
// during its grace and, after it, leaves only the acceptance screen.

const USER = {
  id: 'user-1',
  firstName: 'Anna',
  lastName: 'Muster',
  email: 'anna@example.ch',
  role: 'sales',
  accessRoles: ['sales'],
  isDealerManager: true,
}

function session(overrides: Record<string, unknown> = {}) {
  return {
    user: USER,
    activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
    memberships: [],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ALL_GATED_CAPABILITIES,
    isActiveDealershipManager: true,
    legalAcceptanceDue: null,
    ...overrides,
  }
}

const due = (held: boolean) => ({
  documents: [{ id: 'doc-dpa', documentType: 'dpa', version: '1.1', language: 'de', effectiveDate: '2026-11-01', title: 'AVV 1.1', draft: false }],
  dueBy: '2026-12-01',
  held,
})

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

function app(route: string, me: Record<string, unknown>, extra: FakeRoute[] = []) {
  installFakeBackend([{ method: 'GET', match: /\/auth\/me$/, handler: () => me }, ...extra])
  return renderWithProviders(
    <Routes>
      <Route
        path="*"
        element={
          <ProtectedRoute>
            <Where />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { route },
  )
}

afterEach(() => {
  cleanup()
})

describe('the activation gate in the SPA (KAN-214)', () => {
  it('lands the manager of a Betrieb being set up on the checklist', async () => {
    app('/customers', session({ activeDealership: { id: 'dealership-1', legalName: 'G', status: 'pending_onboarding' } }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/administration/setup'))
  })

  it('lets that manager work in the administration and in My account', async () => {
    app('/administration/business', session({ activeDealership: { id: 'dealership-1', legalName: 'G', status: 'pending_onboarding' } }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/administration/business'))
  })

  it('shows a colleague «Betrieb wird eingerichtet»', async () => {
    app(
      '/stock',
      session({
        activeDealership: { id: 'dealership-1', legalName: 'G', status: 'pending_onboarding' },
        isActiveDealershipManager: false,
      }),
    )
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/setup-in-progress'))
  })

  it('leaves an active Betrieb where it is', async () => {
    app('/customers', session())
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/customers'))
  })
})

describe('the legal hold in the SPA (KAN-214, FR-A-39)', () => {
  it('prompts the manager during the grace, and «Später» closes it', async () => {
    const user = userEvent.setup()
    app('/customers', session({ legalAcceptanceDue: due(false) }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.acceptance.promptTitle') })
    expect(dialog).toHaveTextContent(i18n.t('setup.documentTypes.dpa'))
    expect(screen.getByTestId('where')).toHaveTextContent('/customers')
    await user.click(screen.getByRole('button', { name: i18n.t('setup.acceptance.later') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('after the grace leaves the manager only the acceptance screen', async () => {
    app('/customers', session({ legalAcceptanceDue: due(true) }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/administration/legal-acceptance'))
  })
})

describe('LegalAcceptancePage (KAN-214)', () => {
  it('accepts every due document once each is ticked', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /\/auth\/me$/, handler: () => session({ legalAcceptanceDue: due(true) }) },
      {
        method: 'GET',
        match: /^\/legal-documents\/current$/,
        handler: () => ({
          items: [
            { ...due(true).documents[0], bodyMarkdown: '## 1\n\nText.', acceptanceGraceDays: 30, publishedAt: '2026-11-01T00:00:00Z' },
            { id: 'doc-tos', documentType: 'tos', version: '1.0', language: 'de', effectiveDate: '2026-01-01', title: 'AGB 1.0', draft: false, bodyMarkdown: 'x', acceptanceGraceDays: 30, publishedAt: '2026-01-01T00:00:00Z' },
          ],
        }),
      },
      { method: 'POST', match: /\/legal-acceptances$/, handler: () => status(201, { id: 'acc' }) },
    ])
    renderWithProviders(<LegalAcceptancePage />, { route: '/administration/legal-acceptance' })
    expect(await screen.findByText(i18n.t('setup.acceptance.held'))).toBeInTheDocument()
    // Only the due document (the DPA) is offered — the ToS is already accepted.
    const box = await screen.findByRole('checkbox')
    const submit = screen.getByRole('button', { name: i18n.t('setup.acceptance.submit') })
    expect(submit).toBeDisabled()
    await user.click(box)
    await user.click(submit)
    await waitFor(() => expect(backend.calls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(backend.calls.find((c) => c.method === 'POST')?.body).toEqual({ legalDocumentId: 'doc-dpa' })
  })
})
