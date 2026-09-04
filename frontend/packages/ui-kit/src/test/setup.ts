// Shared vitest setup, loaded for EVERY test file (see vite.config.ts
// `setupFiles`). The default environment is `node`, so this file must stay
// harmless there: registering jest-dom's matchers is environment-free, and
// the RTL cleanup + DOM shims are only wired when a DOM actually exists (a
// render test that declared `// @vitest-environment jsdom`).
import { afterEach, vi } from 'vitest'
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

afterEach(async () => {
  vi.unstubAllGlobals()
  if (typeof document !== 'undefined') {
    const { cleanup } = await import('@testing-library/react')
    cleanup()
    try {
      window.localStorage.clear()
    } catch {
      // no-op — some environments deny storage access
    }
  }
})
