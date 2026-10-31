// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { useTranslation } from 'react-i18next'

import i18n from './index'
import {
  FIRST_PAINT_BUDGET_MS,
  flushMissingReports,
  reloadPublishedTranslations,
  resetRuntimeTranslationsForTests,
  startRuntimeTranslations,
} from './runtimeTranslations'
import { installFakeBackend, status, type FakeBackend } from '../test/fakeBackend'

// KAN-209 (FR-A-30…32) — the platform's published UI strings at runtime.
// The chain, per key: published value → shipped bundle → English → the loud
// marker, reported to the missing-translation log. A published change shows
// without a rebuild: the same running bundle, a new map from the API.

let published: Record<string, Record<string, string>>
let version: number
let backend: FakeBackend

function map(language: string) {
  return { language, version, values: published[language] ?? {} }
}

function Probe() {
  const { t } = useTranslation()
  return (
    <>
      <span data-testid="published">{t('common.retry')}</span>
      <span data-testid="bundle">{t('common.cancel')}</span>
      <span data-testid="absent">{t('kan209.inNoBundle')}</span>
    </>
  )
}

beforeEach(() => {
  published = { de: { 'common.retry': 'Nochmals probieren' } }
  version = 1
  backend = installFakeBackend([
    { method: 'GET', match: /^\/i18n\/(de|fr|it|en)$/, handler: (req) => map(req.pathname.split('/').pop()!) },
    { method: 'POST', match: /^\/i18n\/missing-translations$/, handler: () => status(202, { recorded: 1 }) },
  ])
})

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('de')
  })
  resetRuntimeTranslationsForTests()
})

describe('runtime UI strings (KAN-209)', () => {
  it('resolves published value → bundle → marker, and asks without credentials', async () => {
    await act(async () => {
      await startRuntimeTranslations()
    })
    render(<Probe />)
    expect(screen.getByTestId('published')).toHaveTextContent('Nochmals probieren')
    expect(screen.getByTestId('bundle')).toHaveTextContent('Abbrechen')
    expect(screen.getByTestId('absent')).toHaveTextContent('⚠ MISSING I18N KEY: kan209.inNoBundle')

    const calls = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit | undefined][] } }).mock.calls
    const mapCall = calls.find(([url]) => String(url).endsWith('/i18n/de'))
    expect(mapCall?.[1]?.credentials).toBe('omit')
    // English, the fallback, is fetched at start too.
    expect(backend.callsTo(/^\/i18n\/en$/, 'GET')).toHaveLength(1)
  })

  it('shows a changed value without a rebuild, and the bundle again once it is taken back', async () => {
    await act(async () => {
      await startRuntimeTranslations()
    })
    render(<Probe />)
    expect(screen.getByTestId('published')).toHaveTextContent('Nochmals probieren')

    published = { de: { 'common.retry': 'Neu versuchen', 'common.cancel': 'Lassen' } }
    version = 2
    await act(async () => {
      await reloadPublishedTranslations()
    })
    expect(screen.getByTestId('published')).toHaveTextContent('Neu versuchen')
    expect(screen.getByTestId('bundle')).toHaveTextContent('Lassen')

    published = { de: {} }
    version = 3
    await act(async () => {
      await reloadPublishedTranslations()
    })
    expect(screen.getByTestId('published')).toHaveTextContent('Erneut versuchen')
    expect(screen.getByTestId('bundle')).toHaveTextContent('Abbrechen')
  })

  it('a switch shows the new language with its published values at once, fetched with its bundle', async () => {
    published.fr = { 'common.retry': 'Encore une fois' }
    await act(async () => {
      await startRuntimeTranslations()
    })
    render(<Probe />)
    await act(async () => {
      await i18n.changeLanguage('fr')
    })
    expect(screen.getByTestId('published')).toHaveTextContent('Encore une fois')
    expect(screen.getByTestId('bundle')).toHaveTextContent('Annuler')
    expect(backend.callsTo(/^\/i18n\/fr$/, 'GET')).toHaveLength(1)
  })

  it('keeps the bundle when the map cannot be fetched', async () => {
    installFakeBackend([{ method: 'GET', match: /^\/i18n\//, handler: () => status(503, null) }])
    await act(async () => {
      await startRuntimeTranslations()
    })
    render(<Probe />)
    expect(screen.getByTestId('published')).toHaveTextContent('Erneut versuchen')
  })

  it('never holds the first paint longer than its budget; a late map still lands', async () => {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    installFakeBackend([
      {
        method: 'GET',
        match: /^\/i18n\/(de|fr|it|en)$/,
        handler: async (req) => {
          await held
          return map(req.pathname.split('/').pop()!)
        },
      },
    ])
    const startedAt = Date.now()
    await act(async () => {
      await startRuntimeTranslations()
    })
    const waited = Date.now() - startedAt
    expect(waited).toBeGreaterThanOrEqual(FIRST_PAINT_BUDGET_MS - 50)
    expect(waited).toBeLessThan(FIRST_PAINT_BUDGET_MS + 1000)

    render(<Probe />)
    expect(screen.getByTestId('published')).toHaveTextContent('Erneut versuchen')
    await act(async () => {
      release()
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(screen.getByTestId('published')).toHaveTextContent('Nochmals probieren')
  })

  it('reports a key missing everywhere once per language, in one batch', async () => {
    await act(async () => {
      await startRuntimeTranslations()
    })
    const { rerender } = render(<Probe />)
    rerender(<Probe />)
    await act(async () => {
      await flushMissingReports()
    })
    const reports = backend.callsTo(/^\/i18n\/missing-translations$/, 'POST')
    expect(reports).toHaveLength(1)
    expect(reports[0].body).toEqual({ language: 'de', keys: ['kan209.inNoBundle'] })

    rerender(<Probe />)
    await act(async () => {
      await flushMissingReports()
    })
    expect(backend.callsTo(/^\/i18n\/missing-translations$/, 'POST')).toHaveLength(1)
  })

  it('reaches no network and reports nothing while it is not started (every other test)', async () => {
    render(<Probe />)
    await act(async () => {
      await flushMissingReports()
    })
    expect(backend.calls).toHaveLength(0)
  })
})
