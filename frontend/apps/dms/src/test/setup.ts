// Shared vitest setup, loaded for EVERY test file (see vite.config.ts
// `setupFiles`). The default environment is `node`, so this file must stay
// harmless there: registering jest-dom's matchers is environment-free, and
// the RTL cleanup + DOM shims are only wired when a DOM actually exists (a
// render test that declared `// @vitest-environment jsdom`).
import { afterEach, beforeEach, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

if (typeof window !== 'undefined') {
  // Node 26 exposes its own `--localstorage-file`-gated `localStorage`
  // global, which shadows jsdom's `window.localStorage` and throws on use.
  // The app's `persistedPreferenceStorage` is written to survive that, but
  // the render tests assert on the mirror, so give them a working
  // in-memory Storage. Behaviour under test is unaffected either way.
  const storageWorks = (() => {
    try {
      window.localStorage.setItem('__probe__', '1')
      window.localStorage.removeItem('__probe__')
      return true
    } catch {
      return false
    }
  })()
  if (!storageWorks) {
    const mem = new Map<string, string>()
    const storage: Storage = {
      get length() {
        return mem.size
      },
      clear: () => mem.clear(),
      getItem: (key) => (mem.has(key) ? mem.get(key)! : null),
      key: (index) => Array.from(mem.keys())[index] ?? null,
      removeItem: (key) => void mem.delete(key),
      setItem: (key, value) => void mem.set(String(key), String(value)),
    }
    Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
    Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  }

  // jsdom ships neither of these, and Mantine's Popover/Select/ScrollArea
  // and TanStack Virtual reach for them on mount. No behaviour under test
  // depends on their real semantics — a windowed grid still renders its
  // header, a Select still opens — so inert stand-ins are enough.
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
}

// KAN-172 — a render test never reaches the network. Under
// renderWithProviders, AuthProvider sends GET /auth/me on mount; in a file
// that installed no fake backend that went to the real localhost:8000, and
// when it settled after vitest had torn jsdom down, React's state update
// threw "window is not defined" and failed the run — on whichever PR was
// unlucky. So a jsdom test's default `fetch` is this stand-in: it records
// the request and never settles (nothing lands after teardown), and the
// test fails below, naming it. `installFakeBackend` replaces it. It is the
// file's baseline `fetch`, not a per-test stub, so `vi.unstubAllGlobals()`
// restores the stand-in, never the real fetch: a request fired after a test
// (an unmount's debounced save, say) still never reaches the network.
let unstubbedRequests: string[] = []
if (typeof window !== 'undefined') {
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    unstubbedRequests.push(`${(init?.method ?? 'GET').toUpperCase()} ${url}`)
    return new Promise<Response>(() => {})
  }
  beforeEach(() => {
    unstubbedRequests = []
  })
}

afterEach(async () => {
  // Unmount first, while the test's own fetch (its fake backend) is still
  // installed: a request sent during unmount belongs to that test (KAN-172).
  if (typeof document !== 'undefined') {
    const { cleanup } = await import('@testing-library/react')
    cleanup()
  }
  vi.unstubAllGlobals()
  if (typeof document !== 'undefined') {
    try {
      window.localStorage.clear()
    } catch {
      // no-op — some environments deny storage access
    }
  }
  if (unstubbedRequests.length > 0) {
    const requests = unstubbedRequests
    unstubbedRequests = []
    throw new Error(
      `This render test called fetch without a fake backend: ${requests.join(', ')}. ` +
        'Call installFakeBackend (src/test/fakeBackend.ts) in the test or its beforeEach.',
    )
  }
})
