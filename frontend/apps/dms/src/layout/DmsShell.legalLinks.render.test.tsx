// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { DmsShell } from './DmsShell'

// KAN-452 — the signed-in shell links the public Terms of Use and Privacy
// Policy at the foot of the account menu (the shell has no footer; account
// chrome lives in the sidebar's foot), in a new tab so the screen underneath
// is never lost.

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
const JSDOM_WIDTH = window.innerWidth

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
}

afterEach(async () => {
  setViewportWidth(JSDOM_WIDTH)
  window.localStorage.clear()
  await i18n.changeLanguage('de')
})

describe('DmsShell — legal links in the account menu (KAN-452)', () => {
  it.each(LANGUAGES)('offers the Terms of Use and the Privacy Policy, each in a new tab (%s)', async (lng) => {
    await i18n.loadLanguages(lng)
    const tr = i18n.getFixedT(lng)
    setViewportWidth(1920)
    installFakeBackend([
      {
        method: 'GET',
        match: /\/me\/preferences\/ui$/,
        handler: () => ({ payload: { schemaVersion: 1, sidebarCollapsed: false, uiLanguage: lng, density: 'default' } }),
      },
    ])
    renderWithProviders(
      <DmsShell>
        <p>content</p>
      </DmsShell>,
    )

    const nav = await screen.findByRole('navigation', { name: tr('shell.sidebar.mainNavigation') })
    await userEvent.click(within(nav).getByRole('button', { name: tr('shell.sidebar.accountMenu') }))

    const terms = await screen.findByRole('menuitem', { name: tr('legal.termsLink') })
    const privacy = screen.getByRole('menuitem', { name: tr('legal.privacyLink') })
    expect(terms).toHaveAttribute('href', '/legal/terms')
    expect(privacy).toHaveAttribute('href', '/legal/privacy')
    for (const link of [terms, privacy]) {
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    }
    expect(document.body.textContent).not.toContain('MISSING I18N KEY')
  })
})
