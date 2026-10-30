// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'

// KAN-438 — a user whose last language in this browser was French opens the
// app in French: i18next starts in the `ui` preference mirror's language, and
// main.tsx paints only once `i18nReady` has resolved, so German never shows
// first (src/architecture/main-waits-for-i18n.test.ts pins main.tsx). The
// French bundle is held back here, as a slow network would.
const gate = vi.hoisted(() => {
  let release: () => void = () => {}
  const arrived = new Promise<void>((resolve) => {
    release = resolve
  })
  return { arrived, release: () => release() }
})
vi.mock('./locales/fr.json', async () => {
  await gate.arrived
  return { default: { common: { retry: 'Réessayer' } } }
})

window.localStorage.setItem('dms.preferences.ui', JSON.stringify({ schemaVersion: 1, uiLanguage: 'fr' }))
const { default: i18n, i18nReady, storedUiLanguage } = await import('./index')

describe('the start language is the one the user last chose (KAN-438)', () => {
  it('starts in the stored language and is ready only once its bundle is in', async () => {
    expect(storedUiLanguage()).toBe('fr')
    let ready = false
    void i18nReady.then(() => {
      ready = true
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    // Held back: not ready, so main.tsx has painted nothing — no German frame.
    expect(ready).toBe(false)
    expect(i18n.hasResourceBundle('fr', 'translation')).toBe(false)

    gate.release()
    await i18nReady
    expect(i18n.language).toBe('fr')
    expect(i18n.t('common.retry')).toBe('Réessayer')
    expect(i18n.hasResourceBundle('it', 'translation')).toBe(false)
  })

  it('falls back to German for a stored value that is no UI language, and when nothing is stored', () => {
    window.localStorage.setItem('dms.preferences.ui', JSON.stringify({ uiLanguage: 'es' }))
    expect(storedUiLanguage()).toBe('de')
    window.localStorage.removeItem('dms.preferences.ui')
    expect(storedUiLanguage()).toBe('de')
    window.localStorage.setItem('dms.preferences.ui', '{not json')
    expect(storedUiLanguage()).toBe('de')
  })

})
