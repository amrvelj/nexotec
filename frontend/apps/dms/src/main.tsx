import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { theme } from '@nexotec/ui-kit'
// WP-6c: the real CSS custom properties every token in the ui-kit resolves
// to (tokens.ts exports `var(--x)` strings, not hex literals) — must be
// mounted globally before anything renders, same reason @mantine/core's
// own stylesheet is imported here rather than per-component.
import '@nexotec/ui-kit/src/tokens.css'
import '@mantine/core/styles.css'
import './index.css'
import { i18nReady } from './i18n'
import App from './App.tsx'

// Server state (§ Stack Decisions: TanStack Query) — cursor pagination,
// cache, scroll-position restore on back navigation for every list.
const queryClient = new QueryClient()

// KAN-438: the first paint waits for the start language's bundle, so a user
// whose last language was French or Italian never sees German first. German
// and English are in the bundle, so for them this resolves at once.
void i18nReady.then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <MantineProvider theme={theme}>
          <App />
        </MantineProvider>
      </QueryClientProvider>
    </StrictMode>,
  )
})
