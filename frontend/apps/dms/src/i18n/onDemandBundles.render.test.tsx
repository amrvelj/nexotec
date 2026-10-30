// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { useTranslation } from 'react-i18next'

import i18n, { onDemandBundleLoaders } from './index'

// KAN-438 — only German (the start language) and English (the fallback) ship
// with the app; French and Italian are fetched on the first switch. The French
// bundle here is held back by a gate, the way a slow network holds back its
// chunk, so the test can look at the screen while the switch is pending.
let releaseFrench: () => void = () => {}
const frenchArrives = new Promise<void>((resolve) => {
  releaseFrench = resolve
})
onDemandBundleLoaders.fr = async () => {
  await frenchArrives
  return { default: { common: { retry: 'Réessayer' } } }
}

function Probe() {
  const { t } = useTranslation()
  return (
    <>
      <span data-testid="known">{t('common.retry')}</span>
      <span data-testid="absent">{t('kan438.notInAnyBundle')}</span>
    </>
  )
}

describe('locale bundles load on demand (KAN-438)', () => {
  it('starts with German and English only, and switches to French only once its bundle has arrived', async () => {
    expect(i18n.language).toBe('de')
    expect(i18n.hasResourceBundle('de', 'translation')).toBe(true)
    expect(i18n.hasResourceBundle('en', 'translation')).toBe(true)
    expect(i18n.hasResourceBundle('fr', 'translation')).toBe(false)
    expect(i18n.hasResourceBundle('it', 'translation')).toBe(false)

    // First paint: German at once, no keys, the missing-key marker unchanged.
    render(<Probe />)
    expect(screen.getByTestId('known')).toHaveTextContent('Erneut versuchen')
    expect(screen.getByTestId('absent')).toHaveTextContent('⚠ MISSING I18N KEY: kan438.notInAnyBundle')

    let switched = false
    let switching: Promise<unknown> = Promise.resolve()
    act(() => {
      switching = i18n.changeLanguage('fr').then(() => {
        switched = true
      })
    })
    // While the bundle is on its way the screen stays German: no keys, no
    // English, no half-switched state.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(switched).toBe(false)
    expect(i18n.language).toBe('de')
    expect(screen.getByTestId('known')).toHaveTextContent('Erneut versuchen')

    await act(async () => {
      releaseFrench()
      await switching
    })
    expect(i18n.language).toBe('fr')
    expect(screen.getByTestId('known')).toHaveTextContent('Réessayer')
    expect(screen.getByTestId('absent')).toHaveTextContent('⚠ MISSING I18N KEY: kan438.notInAnyBundle')
    expect(i18n.hasResourceBundle('it', 'translation')).toBe(false)
  })

  it('falls back to English for a key the fetched bundle lacks, as before', async () => {
    await act(async () => {
      await i18n.changeLanguage('fr')
    })
    expect(i18n.t('common.refresh')).toBe(i18n.getResource('en', 'translation', 'common.refresh'))
  })

  it('shows English when every read of a bundle fails, fetches again on the next switch, and retries a failed read', async () => {
    const real = onDemandBundleLoaders.it!
    let calls = 0
    onDemandBundleLoaders.it = () => {
      calls += 1
      return Promise.reject(new Error('chunk not reachable'))
    }
    try {
      await act(async () => {
        await i18n.changeLanguage('it')
      })
      expect(calls).toBe(3) // the first read and its two retries
      expect(i18n.hasResourceBundle('it', 'translation')).toBe(false)
      expect(i18n.t('common.retry')).toBe(i18n.getResource('en', 'translation', 'common.retry'))
      expect(i18n.t('kan438.notInAnyBundle')).toBe('⚠ MISSING I18N KEY: kan438.notInAnyBundle')

      // Next switch: asked for again; this time the first read fails and the
      // retry brings it.
      calls = 0
      onDemandBundleLoaders.it = () => {
        calls += 1
        return calls < 2 ? Promise.reject(new Error('chunk not reachable')) : real()
      }
      await act(async () => {
        await i18n.changeLanguage('de')
      })
      await act(async () => {
        await i18n.changeLanguage('it')
      })
      expect(calls).toBe(2)
      expect(i18n.language).toBe('it')
      expect(i18n.hasResourceBundle('it', 'translation')).toBe(true)
      expect(i18n.t('common.retry')).toBe(i18n.getResource('it', 'translation', 'common.retry'))
      expect(i18n.t('common.retry')).not.toBe('Erneut versuchen')
    } finally {
      onDemandBundleLoaders.it = real
      await act(async () => {
        await i18n.changeLanguage('de')
      })
    }
  })
})
