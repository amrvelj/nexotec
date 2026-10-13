/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ALL_SPEC_FIELDS } from '../configurationOptions'

// KAN-168 / ADR-076: every CHF amount reads `CHF 12'500.00`, inputs included.
// A CHF input is the kit's ChfInput (or `chfInlineEditor` inside an
// InlineEditField); a Mantine NumberInput shows `12500`. oxlint has no
// custom-rule mechanism, so this is a build-failing scan, like
// no-locale-number-format.test.ts beside it.
//
// A NumberInput is still right for what is not money — a quantity, months,
// kilometres, a percentage, a spec figure. Each one left is named below by its
// label, so a new NumberInput fails here until someone decides it is not a CHF
// amount and adds its label, in a diff a reviewer sees.

const APP_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

const NOT_MONEY_LABELS = new Set([
  "t('offerWorkspace.lineItems.accessoryQuantity')", // a count
  "t('offerWorkspace.leasing.termMonths')", // months
  "t('offerWorkspace.leasing.kmPerYear')", // kilometres
  "t('valuationCreate.mileage')", // kilometres
  "t('valuationCreate.validForDays')", // days
  "t('configurator.summary.mileage')", // kilometres
  "t('vehicleDetail.odometer.value')", // kilometres
  // PriceBuildUp: the discount value as a percentage (or disabled with no
  // discount type); an amount discount renders a ChfInput instead — the label
  // is shared, so the last test below pins the ChfInput branch.
  "t('offerWorkspace.pricing.discountValue')",
  // ConfiguratorOverlay: the spec block's `int` and `decimal` figures (power,
  // weights, consumption, years); a `chf` field renders a ChfInput instead —
  // the last test below pins which fields are `chf`.
  'withMark',
])

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) collectFiles(full, out)
    else if (entry.endsWith('.tsx') && !entry.includes('.test.')) out.push(full)
  }
  return out
}

interface NumberInputUse {
  where: string
  label: string | null
}

function numberInputUses(file: string): NumberInputUse[] {
  const source = readFileSync(file, 'utf8')
  const uses: NumberInputUse[] = []
  for (const match of source.matchAll(/<NumberInput\b/g)) {
    const start = match.index ?? 0
    const end = source.indexOf('/>', start)
    const element = source.slice(start, end === -1 ? undefined : end)
    const label = element.match(/\blabel=\{([^}]*\)?)\}/)?.[1]?.trim() ?? null
    const line = source.slice(0, start).split('\n').length
    uses.push({ where: `${relative(APP_ROOT, file)}:${line}`, label })
  }
  return uses
}

describe('a CHF amount is never typed into a bare NumberInput (KAN-168)', () => {
  const files = collectFiles(APP_ROOT)
  const uses = files.flatMap(numberInputUses)

  it('every NumberInput left in apps/dms is named as not money', () => {
    // An empty walk would pass vacuously.
    expect(uses.length).toBeGreaterThan(0)
    const unnamed = uses.filter((u) => u.label === null || !NOT_MONEY_LABELS.has(u.label))
    expect(unnamed.map((u) => `${u.where} label=${u.label ?? '(none)'}`)).toEqual([])
  })

  it('every name on the not-money list is still in use (no stale exemptions)', () => {
    const labels = new Set(uses.map((u) => u.label))
    expect([...NOT_MONEY_LABELS].filter((label) => !labels.has(label))).toEqual([])
  })

  it('no raw <input type="number"> stands in for either', () => {
    const offenders = files.filter((file) => /type=["']number["']/.test(readFileSync(file, 'utf8')))
    expect(offenders.map((file) => relative(APP_ROOT, file))).toEqual([])
  })

  it('the CHF inputs the ticket names render through the kit', () => {
    const chfUses = files.filter((file) => /<ChfInput\b|chfInlineEditor\(/.test(readFileSync(file, 'utf8')))
    expect(chfUses.map((file) => relative(APP_ROOT, file)).sort()).toEqual(
      [
        'components/OfferAccessoriesAndOptions.tsx',
        'components/PriceBuildUp.tsx',
        'components/ValuationCreateDialog.tsx',
        'components/configurator/ColourWheelsTab.tsx',
        'components/configurator/ConfiguratorOverlay.tsx',
        'components/configurator/IdentificationPanel.tsx',
        'components/customer-detail/OverviewTab.tsx',
        'components/stock-detail/DetailsTab.tsx',
        'components/stock-detail/RecordCostDialog.tsx',
        'components/stock-detail/RecordPurchaseDialog.tsx',
        'pages/OfferWorkspacePage.tsx',
      ].sort(),
    )
  })

  it('the two shared-label exemptions cannot hide a CHF amount', () => {
    // The discount label covers a percentage (NumberInput) and an amount
    // (ChfInput): exactly one of each, so the amount cannot turn back into
    // a NumberInput unnoticed.
    const priceBuildUp = readFileSync(join(APP_ROOT, 'components/PriceBuildUp.tsx'), 'utf8')
    const discountLabel = /label=\{t\('offerWorkspace\.pricing\.discountValue'\)\}/
    const elements = [...priceBuildUp.matchAll(/<(ChfInput|NumberInput)\b/g)].map((m) =>
      priceBuildUp.slice(m.index ?? 0, priceBuildUp.indexOf('/>', m.index ?? 0)),
    )
    const discount = elements.filter((element) => discountLabel.test(element)).map((element) => element.split(/\s/)[0])
    expect(discount.sort()).toEqual(['<ChfInput', '<NumberInput'])

    // Every spec-block field that holds a price is a `chf` field (ChfInput).
    const priced = ALL_SPEC_FIELDS.filter((f) => /price|surcharge|amount|cost/i.test(String(f.key)))
    expect(priced.length).toBeGreaterThan(0)
    expect(priced.filter((f) => f.kind !== 'chf' && !/year/i.test(String(f.key))).map((f) => f.key)).toEqual([])
  })
})
