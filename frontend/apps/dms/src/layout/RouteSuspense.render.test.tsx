// @vitest-environment jsdom
import { Component, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { BreadcrumbProvider, theme } from '@nexotec/ui-kit'
import i18n from '../i18n'
import { ChunkLoadError, lazyPage } from './lazyPage'
import { RouteSuspense } from './RouteSuspense'

// KAN-233: every screen but the sign-in is fetched on demand. A tab opened
// before a deploy asks for hashed files the deploy replaced; that failure
// must read as a translated message with a reload, never a white screen.

const TITLE: Record<'de' | 'fr' | 'it' | 'en', string> = {
  de: 'Diese Ansicht konnte nicht geladen werden',
  fr: "Cet écran n'a pas pu être chargé",
  it: 'Impossibile caricare questa schermata',
  en: 'This screen could not be loaded',
}
const RELOAD: Record<keyof typeof TITLE, string> = {
  de: 'Seite neu laden',
  fr: 'Recharger la page',
  it: 'Ricarica la pagina',
  en: 'Reload page',
}

function OtherPage() {
  return <div>other screen</div>
}

class OuterBoundary extends Component<{ children: ReactNode }, { caught: string | null }> {
  state = { caught: null as string | null }
  static getDerivedStateFromError(error: Error) {
    return { caught: error.message }
  }
  render() {
    return this.state.caught ? <div>outer caught: {this.state.caught}</div> : this.props.children
  }
}

function renderRoutes(page: ReturnType<typeof lazyPage>, setBreadcrumb: (segments: string[]) => void = () => {}) {
  const Page = page
  return render(
    <MantineProvider theme={theme} env="test" withCssVariables={false} withGlobalClasses={false}>
      <BreadcrumbProvider value={setBreadcrumb}>
        <MemoryRouter initialEntries={['/lazy']}>
          {/* The shell's navigation, outside the boundary as in DmsShell. */}
          <Link to="/other">go elsewhere</Link>
          <OuterBoundary>
            <RouteSuspense fill={false}>
              <Routes>
                <Route path="/lazy" element={<Page />} />
                <Route path="/other" element={<OtherPage />} />
              </Routes>
            </RouteSuspense>
          </OuterBoundary>
        </MemoryRouter>
      </BreadcrumbProvider>
    </MantineProvider>,
  )
}

afterEach(async () => {
  vi.restoreAllMocks()
  await i18n.changeLanguage('de')
})

describe('lazy route screens', () => {
  it('shows the loading state, then the screen once its chunk arrives', async () => {
    let resolve: (m: { LazyScreen: () => ReactNode }) => void = () => {}
    const loaded = new Promise<{ LazyScreen: () => ReactNode }>((r) => (resolve = r))
    renderRoutes(lazyPage(() => loaded, 'LazyScreen'))

    expect(document.querySelector('.mantine-Loader-root')).not.toBeNull()
    expect(screen.queryByText('lazy screen')).toBeNull()

    resolve({ LazyScreen: () => <div>lazy screen</div> })
    expect(await screen.findByText('lazy screen')).toBeInTheDocument()
  })

  for (const lng of ['de', 'fr', 'it', 'en'] as const) {
    it(`a chunk that fails to load shows a translated reload screen in ${lng}`, async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {})
      await i18n.changeLanguage(lng)
      renderRoutes(lazyPage(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module')), 'X'))

      expect(await screen.findByText(TITLE[lng])).toBeInTheDocument()
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: RELOAD[lng] })).toBeInTheDocument()
      expect(screen.queryByText(/Failed to fetch/)).toBeNull()
      expect(screen.queryByText(/MISSING I18N KEY/)).toBeNull()
    })
  }

  it("replaces the previous screen's breadcrumb with the failure", async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const setBreadcrumb = vi.fn()
    renderRoutes(lazyPage(() => Promise.reject(new Error('chunk gone')), 'X'), setBreadcrumb)

    await screen.findByText(TITLE.de)
    expect(setBreadcrumb).toHaveBeenLastCalledWith([TITLE.de])
  })

  it('the reload action reloads the page', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload })
    renderRoutes(lazyPage(() => Promise.reject(new Error('chunk gone')), 'X'))

    fireEvent.click(await screen.findByRole('button', { name: RELOAD.de }))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('leaving a screen that failed renders the next one', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    renderRoutes(lazyPage(() => Promise.reject(new Error('chunk gone')), 'X'))
    await screen.findByText(TITLE.de)

    fireEvent.click(screen.getByText('go elsewhere'))
    expect(await screen.findByText('other screen')).toBeInTheDocument()
    expect(screen.queryByText(TITLE.de)).toBeNull()
  })

  it('an error thrown while rendering a loaded screen is not taken for a failed chunk', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    function Broken(): ReactNode {
      throw new Error('render bug')
    }
    renderRoutes(lazyPage(() => Promise.resolve({ Broken }), 'Broken'))

    expect(await screen.findByText('outer caught: render bug')).toBeInTheDocument()
    expect(screen.queryByText(TITLE.de)).toBeNull()
  })

  it('ChunkLoadError carries the import failure it wraps', () => {
    const error = new ChunkLoadError(new TypeError('Importing a module script failed.'))
    expect(error).toBeInstanceOf(Error)
    expect(error.name).toBe('ChunkLoadError')
    expect(error.message).toBe('Importing a module script failed.')
    expect(error.cause).toBeInstanceOf(TypeError)
  })
})
