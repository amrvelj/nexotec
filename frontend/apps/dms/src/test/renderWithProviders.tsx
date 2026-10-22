import { StrictMode, type ReactElement } from 'react'
import { render, type RenderResult } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { theme } from '@nexotec/ui-kit'
import { AuthProvider } from '../auth/AuthContext'
import { UiPreferencesProvider } from '../hooks/UiPreferencesContext'
import '../i18n'
import { TranslatedOverlayProvider } from './TranslatedOverlayProvider'

export interface RenderOptions {
  /** Initial entry for the MemoryRouter — this IS the "pasted URL". */
  route?: string
  queryClient?: QueryClient
  /** Mount under a root `<StrictMode>`, as `main.tsx` does, so React runs a
   * mounting component's effects twice, as in development. A `<StrictMode>`
   * passed inside `ui` did not double-run them here. */
  strictMode?: boolean
}

export interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient
}

/**
 * Mounts `ui` under the same provider stack DmsShell gives every screen:
 * Mantine, TanStack Query, the router, auth, the shared UI-preference
 * scope, and the ADR-059 overlay stack. No screen is stubbed — a test
 * passes the real page (or a real `<Routes>`), and `installFakeBackend`
 * supplies the data.
 *
 * `withCssVariables={false}` (KAN-325): vitest loads no stylesheet, so the
 * 11 KB of `--mantine-*` variables the provider would inject style nothing
 * here — but jsdom parsed them on every mount (~0.25 s a render) and
 * matched them on every `getComputedStyle`, which every `*ByRole` query
 * makes per candidate for its visibility check. Nothing a test can observe
 * changes: the variables are read by Mantine's CSS, never by its JS.
 */
export function renderWithProviders(ui: ReactElement, options: RenderOptions = {}): RenderWithProvidersResult {
  const queryClient =
    options.queryClient ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0, staleTime: 0 },
        mutations: { retry: false },
      },
    })

  const tree = (
    <MantineProvider theme={theme} env="test" withCssVariables={false} withGlobalClasses={false}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[options.route ?? '/']}>
          <AuthProvider>
            <UiPreferencesProvider>
              <TranslatedOverlayProvider>{ui}</TranslatedOverlayProvider>
            </UiPreferencesProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  )
  const result = render(options.strictMode ? <StrictMode>{tree}</StrictMode> : tree)

  return Object.assign(result, { queryClient })
}
