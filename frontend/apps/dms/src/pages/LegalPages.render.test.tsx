// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { testTheme } from '../test/testTheme'
import i18n from '../i18n'
import App from '../App'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { LoginPage } from './LoginPage'
import { PrivacyPolicyPage } from './PrivacyPolicyPage'
import { TermsOfUsePage } from './TermsOfUsePage'

// KAN-452 — Google's Places API policies require publicly accessible Terms
// of Use and a Privacy Policy that incorporate Google's Terms of Service and
// Privacy Policy. Both pages are served by the SPA without a session, in all
// four languages, and the sign-in screen links to both.

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
const GOOGLE_TERMS = 'https://policies.google.com/terms'
const GOOGLE_PRIVACY = 'https://policies.google.com/privacy'

const SIGNED_OUT = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => status(401, { error: { code: 'unauthorized', message: 'x', details: null } }),
}

const PAGES = [
  { path: '/legal/terms', titleKey: 'legal.terms.title' },
  { path: '/legal/privacy', titleKey: 'legal.privacy.title' },
] as const

function renderLegal(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/legal/terms" element={<TermsOfUsePage />} />
      <Route path="/legal/privacy" element={<PrivacyPolicyPage />} />
      <Route path="/login" element={<p>sign-in screen</p>} />
    </Routes>,
    { route },
  )
}

afterEach(async () => {
  window.history.replaceState(null, '', '/')
  await i18n.changeLanguage('de')
})

describe('the public legal pages (KAN-452)', () => {
  it.each(PAGES)('$path is reachable through the real route table without a session', async ({ path, titleKey }) => {
    installFakeBackend([SIGNED_OUT])
    window.history.replaceState(null, '', path)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MantineProvider theme={testTheme} env="test" withCssVariables={false} withGlobalClasses={false}>
          <App />
        </MantineProvider>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('heading', { level: 1, name: i18n.t(titleKey) })).toBeInTheDocument()
    // Not sent to the sign-in screen: the session guard is not in front of it.
    expect(window.location.pathname).toBe(path)
    expect(screen.queryByRole('button', { name: i18n.t('auth.login.submit') })).not.toBeInTheDocument()
  })

  for (const lng of LANGUAGES) {
    it.each(PAGES)(`$path renders in ${lng} with no missing key and links Google's Terms and Privacy Policy`, async ({ path, titleKey }) => {
      await i18n.loadLanguages(lng)
      await i18n.changeLanguage(lng)
      const tr = i18n.getFixedT(lng)
      installFakeBackend([SIGNED_OUT])
      renderLegal(path)

      expect(await screen.findByRole('heading', { level: 1, name: tr(titleKey) })).toBeInTheDocument()
      expect(document.body.textContent).not.toContain('MISSING I18N KEY')
      // Every other section is a marked placeholder; the page says it is a draft.
      expect(screen.getByText(tr('legal.draftNotice'))).toBeInTheDocument()
      expect(screen.getAllByText(tr('legal.placeholder')).length).toBeGreaterThan(0)

      // The Places section: marked as awaiting counsel, and linking Google's documents.
      const places = screen.getByRole('region', { name: (n) => n.includes(tr(`${titleKey.replace('.title', '')}.sections.places`)) })
      expect(within(places).getByText(tr('legal.reviewPending'))).toBeInTheDocument()
      const hrefs = within(places)
        .getAllByRole('link')
        .map((a) => a.getAttribute('href'))
      expect(hrefs).toEqual(expect.arrayContaining([GOOGLE_TERMS, GOOGLE_PRIVACY]))
      for (const link of within(places).getAllByRole('link')) {
        expect(link).toHaveAttribute('target', '_blank')
      }
    })
  }

  it.each(LANGUAGES)('the Privacy Policy carries the runbook\'s «Address suggestions» paragraph (%s)', async (lng) => {
    await i18n.loadLanguages(lng)
    await i18n.changeLanguage(lng)
    installFakeBackend([SIGNED_OUT])
    renderLegal('/legal/privacy')

    expect(await screen.findByText(i18n.getFixedT(lng)('legal.privacy.places.body'))).toBeInTheDocument()
    // Google named as the receiving controller, the address text only.
    expect(document.body.textContent).toContain('Google Ireland Limited')
  })

  it('?lang= opens the page in that language, and the switch writes it', async () => {
    const user = userEvent.setup()
    await i18n.loadLanguages(['fr', 'en'])
    installFakeBackend([SIGNED_OUT])
    renderLegal('/legal/terms?lang=fr')

    expect(await screen.findByRole('heading', { level: 1, name: i18n.getFixedT('fr')('legal.terms.title') })).toBeInTheDocument()

    await user.click(screen.getByText('EN'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Terms of Use' })).toBeInTheDocument()
  })
})

describe('the sign-in screen links both legal pages (KAN-452)', () => {
  it.each(LANGUAGES)('in %s', async (lng) => {
    await i18n.loadLanguages(lng)
    await i18n.changeLanguage(lng)
    const tr = i18n.getFixedT(lng)
    installFakeBackend([SIGNED_OUT])
    renderWithProviders(
      <Routes>
        <Route path="/login" element={<LoginPage />} />
      </Routes>,
      { route: '/login' },
    )

    const legal = await screen.findByRole('navigation', { name: tr('legal.linksLabel') })
    expect(within(legal).getByRole('link', { name: tr('legal.termsLink') })).toHaveAttribute('href', '/legal/terms')
    expect(within(legal).getByRole('link', { name: tr('legal.privacyLink') })).toHaveAttribute('href', '/legal/privacy')
    expect(document.body.textContent).not.toContain('MISSING I18N KEY')
  })
})
