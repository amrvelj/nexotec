/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-233: every screen but the sign-in is fetched when its route is first
// opened. One static page import in App.tsx — or in a module the entry chunk
// always loads — silently puts that screen, and every library only it uses,
// back into the first download. The build would still pass; this does not.

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (path: string) => readFileSync(join(SRC, path), 'utf-8')

// What the entry chunk loads before any route: the sign-in, the session
// guard, the entitlement gate, the auth provider and the shell.
const EAGER_PAGES = new Set(['./pages/LoginPage'])
const EAGER_MODULES = [
  'App.tsx',
  'pages/LoginPage.tsx',
  'components/ProtectedRoute.tsx',
  'components/RequireEntitlement.tsx',
  'auth/AuthContext.tsx',
  'layout/DmsShell.tsx',
  'layout/RouteSuspense.tsx',
]

const STATIC_PAGE_IMPORT = /^import\s[^;]*?from\s+'((?:\.\.?\/)+(?:[\w-]+\/)*pages\/[^']+)'/gm

function staticPageImports(source: string): string[] {
  return [...source.matchAll(STATIC_PAGE_IMPORT)].map((m) => m[1])
}

describe('route screens load on demand', () => {
  it('App.tsx imports no page statically but the sign-in', () => {
    const app = read('App.tsx')
    expect(staticPageImports(app).filter((path) => !EAGER_PAGES.has(path))).toEqual([])
    // Not vacuous: the routes are there, through lazyPage.
    expect((app.match(/lazyPage\(\(\) => import\('\.\/pages\//g) ?? []).length).toBeGreaterThan(20)
  })

  it('no module the entry chunk always loads imports a page', () => {
    const offenders = EAGER_MODULES.filter((path) => path !== 'App.tsx').flatMap((path) =>
      staticPageImports(read(path)).map((page) => `${path}: ${page}`),
    )
    expect(offenders).toEqual([])
  })

  it("the shell's outlet renders under RouteSuspense", () => {
    expect(read('App.tsx')).toMatch(/<RouteSuspense fill=\{false\}>\s*<Outlet \/>\s*<\/RouteSuspense>/)
  })

  it('the pattern matches the import shapes it exists to stop', () => {
    expect(staticPageImports("import { StockListPage } from './pages/StockListPage'")).toEqual(['./pages/StockListPage'])
    expect(staticPageImports("import { AccountLayout } from './pages/account/AccountLayout'")).toEqual([
      './pages/account/AccountLayout',
    ])
    expect(staticPageImports("import { X } from '../../pages/CustomerDetailPage'")).toEqual(['../../pages/CustomerDetailPage'])
    expect(staticPageImports("const X = lazyPage(() => import('./pages/X'), 'X')")).toEqual([])
  })
})
