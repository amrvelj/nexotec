import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

// C-F (KAN-10) exit criterion 5 — "the summary card is ONE implementation
// rendered in all four hosts. Asserted, not asserted-by-convention: four
// renderings of one record produce four different answers about the same
// car." The render tests prove each host shows the card; this proves no
// host shows a configuration any other way.

const SRC = join(__dirname, '..', '..', '..', '..')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : sourceFiles(path)
    if (!/\.tsx?$/.test(name) || /\.test\.tsx?$/.test(name) || name.endsWith('.d.ts')) return []
    return [path]
  })
}

const FILES = sourceFiles(SRC).map((path) => ({ path: relative(SRC, path), text: readFileSync(path, 'utf8') }))

// A host outside the vehicle module names a card through the module's door,
// src/modules/vehicle/index.ts (ADR-161, KAN-636), which re-exports it.
const throughTheDoor = (text: string, name: string) =>
  new RegExp(`import \\{[^}]*\\b${name}\\b[^}]*\\} from ['"](?:\\.\\.?/)+(?:modules/)?vehicle['"]`).test(text)
const importsCard = (text: string) =>
  /from ['"](?:[^'"]*\/)?ConfigurationSummaryCard['"]/.test(text) || throughTheDoor(text, 'ConfigurationSummaryCard')
const importsHostCard = (text: string) =>
  /from ['"](?:[^'"]*\/)?HostConfigurationCard['"]/.test(text) || throughTheDoor(text, 'HostConfigurationCard')

describe('one configuration summary card', () => {
  it('exactly one component renders the card markup', () => {
    const renderers = FILES.filter((f) => f.text.includes('data-testid="configuration-summary-card"')).map((f) => f.path)
    expect(renderers).toEqual(['modules/vehicle/components/configurator/ConfigurationSummaryCard.tsx'])
  })

  it('only the known wrappers import the card directly', () => {
    const importers = FILES.filter((f) => importsCard(f.text)).map((f) => f.path).sort()
    expect(importers).toEqual([
      'modules/vehicle/components/configurator/ConfiguratorOverlay.tsx',
      'modules/vehicle/components/configurator/HostConfigurationCard.tsx',
      'modules/vehicle/components/vehicle-detail/SpecificationTab.tsx',
      'modules/vehicle/index.ts',
      'modules/valuation/components/ValuationCreateDialog.tsx',
    ].sort())
  })

  it('every host reaches a configuration through the shared card', () => {
    const host = (path: string) => FILES.find((f) => f.path === path)!.text
    // offer workspace and pending contract (Path B and the trade-in; one set
    // of containers since KAN-67), stock item, valuation
    expect(importsHostCard(host('modules/sales/components/deal-workspace/DealWorkspaceContainers.tsx'))).toBe(true)
    const usesDealContainers = /from ['"][^'"]*\/deal-workspace\/DealWorkspaceContainers['"]/
    expect(host('modules/sales/pages/OfferWorkspacePage.tsx')).toMatch(usesDealContainers)
    expect(host('modules/sales/pages/ContractDetailPage.tsx')).toMatch(usesDealContainers)
    expect(importsHostCard(host('modules/stock/components/stock-detail/DetailsTab.tsx'))).toBe(true)
    expect(importsHostCard(host('modules/valuation/pages/ValuationDetailPage.tsx'))).toBe(true)
    // Vehicle 360
    expect(host('modules/vehicle/pages/VehicleDetailPage.tsx')).toMatch(/from ['"]\.\.\/components\/vehicle-detail\/SpecificationTab['"]/)
  })

  it('no second configuration card component exists', () => {
    const cards = FILES.flatMap((f) => [...f.text.matchAll(/export function (\w*Configuration\w*Card)\b/g)].map((m) => m[1])).sort()
    expect(cards).toEqual(['ConfigurationSummaryCard', 'HostConfigurationCard'])
  })
})
