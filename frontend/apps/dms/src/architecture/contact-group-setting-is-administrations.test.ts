/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-466 (A-RULE-0): which contact groups a Betrieb uses is configured on
// one screen, Dealer Administration › Kontaktgruppen. A module screen reads
// the Betrieb's groups (to offer them, to name them), never switches them:
// the only source that writes `/contact-groups/…` is that page, and the only
// route to it is under /administration. The backend half is
// tests/architecture/test_contact_group_setting_is_administrations.py.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SETTING_PAGE = 'pages/ContactGroupsPage.tsx'

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'test' || name === 'architecture' ? [] : sources(path)
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

describe('the contact-group setting is Dealer Administration’s (KAN-466, A-RULE-0)', () => {
  const files = sources(SRC_ROOT).map((path) => ({ rel: relative(SRC_ROOT, path), text: readFileSync(path, 'utf8') }))

  it('only the Kontaktgruppen page writes a contact-group switch', () => {
    const writers = files
      .filter(({ text }) => /api\.(put|post|patch|delete)[^;]*contact-groups/s.test(text))
      .map(({ rel }) => rel)
    expect(writers).toEqual([SETTING_PAGE])
  })

  it('the page is routed under /administration and nowhere else', () => {
    const routed = files.filter(({ text }) => text.includes('<ContactGroupsPage')).map(({ rel }) => rel)
    expect(routed).toEqual(['App.tsx'])
    const app = files.find(({ rel }) => rel === 'App.tsx')!.text
    expect(app).toMatch(/<Route path="\/administration\/contact-groups" element=\{<ContactGroupsPage \/>\} \/>/)
  })
})
