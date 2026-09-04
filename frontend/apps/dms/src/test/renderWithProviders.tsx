import type { ReactElement } from 'react'
import { render, type RenderResult } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { OverlayProvider, theme } from '@nexotec/ui-kit'
import { AuthProvider } from '../auth/AuthContext'
import { UiPreferencesProvider } from '../hooks/UiPreferencesContext'
import '../i18n'

export interface RenderOptions {
  /** Initial entry for the MemoryRouter — this IS the "pasted URL". */
  route?: string
  queryClient?: QueryClient
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

  const result = render(
    <MantineProvider theme={theme} env="test">
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[options.route ?? '/']}>
          <AuthProvider>
            <UiPreferencesProvider>
              <OverlayProvider>{ui}</OverlayProvider>
            </UiPreferencesProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  )

  return Object.assign(result, { queryClient })
}
