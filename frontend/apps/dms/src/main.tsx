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
import './i18n'
import App from './App.tsx'

// Server state (§ Stack Decisions: TanStack Query) — cursor pagination,
// cache, scroll-position restore on back navigation for every list.
const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme}>
        <App />
      </MantineProvider>
    </QueryClientProvider>
  </StrictMode>,
)
