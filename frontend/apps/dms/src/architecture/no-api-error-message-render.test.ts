/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-398 (Anto, 2026-10-23): an API refusal is shown in the UI language.
// A screen maps `details.reason` (and a stale If-Match 409) to a key in all
// four bundles and anything else to a translated generic message — never
// `err.message`, which is the API's English text, meant for logs and
// developers (MergeCustomerModal and LinkVehicleModal, KAN-366/KAN-369, are
// the pattern). oxlint has no custom-rule mechanism, so this is a
// build-failing scan, like the colour and number ones.
//
// The scan flags every `.message` read outside comments, tests and console
// logging — whatever the value is called (`err`, `e`, `query.error`,
// `failureReason?.message`, a refusal's `problem.message`), because a
// narrower pattern is one rename away from blind. Today's sites are
// allow-listed by file with their count — a ratchet: a new read in any file
// fails, and a file that is converted must lower or drop its entry, so the
// list only shrinks. The count is per file, so one conversion and one new
// read in the same file pass together; a reviewer still sees the diff. The
// conversion of the listed sites is KAN-437.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

const ALLOWED: Record<string, number> = {
  'components/CustomerCreateFlow.tsx': 1,
  'components/MappingGapsQueue.tsx': 1,
  'components/OfferAccessoriesAndOptions.tsx': 1,
  'components/OfferGenerateReviewModal.tsx': 2,
  'components/ValuationCreateDialog.tsx': 1,
  // HostCommitError, always built from t(...) by its hosts — translated, so it
  // stays; KAN-437 leaves this entry as it is.
  'components/configurator/ConfiguratorOverlay.tsx': 2,
  'components/customer-detail/ContactPointsEditor.tsx': 1,
  'components/customer-detail/CreditBlockDialog.tsx': 1,
  'components/customer-detail/ExternalIdsTab.tsx': 1,
  'components/customer-detail/OverviewTab.tsx': 1,
  'components/recovery/RecoveryReasonDialog.tsx': 2,
  // A blocking condition's server text (c.message).
  'components/stock-detail/BlockingConditionBanner.tsx': 1,
  'components/stock-detail/ChannelPublishCard.tsx': 2,
  'components/stock-detail/PublishingTab.tsx': 1,
  'components/stock-detail/RecordCostDialog.tsx': 1,
  'components/stock-detail/RecordPurchaseDialog.tsx': 1,
  'components/users/UserDeactivateDialog.tsx': 1,
  'components/vehicle-detail/AccessoriesTab.tsx': 2,
  'components/vehicle-detail/OdometerTab.tsx': 1,
  'components/vehicle/VehicleCreateDialog.tsx': 1,
  'pages/AccountSecurityPage.tsx': 3,
  'pages/ContractDetailPage.tsx': 1,
  'pages/CustomerDetailPage.tsx': 4,
  'pages/CustomersListPage.tsx': 3,
  'pages/IntegrationDealerView.tsx': 1,
  'pages/LoginPage.tsx': 1,
  'pages/OfferDetailPage.tsx': 3,
  'pages/OfferWorkspacePage.tsx': 1,
  'pages/PasswordResetRequestPage.tsx': 1,
  'pages/ReferenceDataPage.tsx': 2,
  'pages/StockDetailPage.tsx': 3,
  'pages/ValuationDetailPage.tsx': 2,
  'pages/ValuationsListPage.tsx': 1,
  'pages/VehicleDetailPage.tsx': 1,
  'pages/auth/passwordErrors.ts': 1,
  // refusalText's fallthrough and a reassignment problem's problem.message.
  'userOptions.ts': 2,
}

// Where an error is built, not shown: ApiError from the response body, and
// ChunkLoadError from the failed import it wraps.
const EXEMPT = new Set(['api/client.ts', 'layout/lazyPage.ts'])

const SCAN_EXTENSIONS = ['.ts', '.tsx']
const SKIP_DIR_NAMES = new Set(['node_modules', 'dist', 'build', '.git'])

const MESSAGE_READ = /\.message\b/

function collectFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIR_NAMES.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      collectFiles(full, out)
    } else if (SCAN_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full)
    }
  }
  return out
}

function offendingLines(source: string): string[] {
  const out: string[] = []
  source.split('\n').forEach((line, index) => {
    const trimmed = line.trim()
    if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return
    if (/\bconsole\s*\./.test(line)) return
    if (MESSAGE_READ.test(line)) out.push(`${index + 1}: ${trimmed}`)
  })
  return out
}

function scan(): Map<string, string[]> {
  const found = new Map<string, string[]>()
  for (const file of collectFiles(SRC_ROOT)) {
    if (/\.test\.tsx?$/.test(file)) continue
    const relative = file.slice(SRC_ROOT.length + 1).split('\\').join('/')
    if (EXEMPT.has(relative)) continue
    const lines = offendingLines(readFileSync(file, 'utf-8'))
    if (lines.length > 0) found.set(relative, lines)
  }
  return found
}

describe("an API refusal is never rendered from the API's English message", () => {
  it('finds no err.message render beyond the allow-listed sites', () => {
    const offenders: string[] = []
    for (const [file, lines] of scan()) {
      const allowed = ALLOWED[file] ?? 0
      if (lines.length > allowed) {
        offenders.push(`${file}: ${lines.length} err.message read(s), ${allowed} allowed\n    ${lines.join('\n    ')}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('lists no file with more allowed sites than it still has', () => {
    const found = scan()
    // An empty walk would pass the ratchet vacuously — the allow-list proves
    // the screens were actually scanned.
    expect(found.size).toBeGreaterThan(0)
    const stale = Object.entries(ALLOWED)
      .filter(([file, allowed]) => (found.get(file)?.length ?? 0) < allowed)
      .map(([file, allowed]) => `${file}: ${found.get(file)?.length ?? 0} left, ${allowed} allowed — lower the entry`)
    expect(stale).toEqual([])
  })

  // The scan passes on an empty result as easily as on a clean tree — this
  // proves the pattern fires on the shapes the rule forbids, and stays quiet
  // on the translated ones.
  it('the pattern matches the render shapes it exists to stop', () => {
    expect(offendingLines("setError(err instanceof ApiError ? err.message : t('x.error'))")).toHaveLength(1)
    expect(offendingLines('{query.error instanceof ApiError ? query.error.message : t(key)}')).toHaveLength(1)
    expect(offendingLines('onError: (err) => setError((err as Error).message),')).toHaveLength(1)
    expect(offendingLines('return mutationError?.message')).toHaveLength(1)
    expect(offendingLines('} catch (e) { setError(e.message) }')).toHaveLength(1)
    expect(offendingLines('{mutation.failureReason?.message}')).toHaveLength(1)
    expect(offendingLines('return problem.message ?? t(key)')).toHaveLength(1)
    expect(offendingLines("setError(mergeRefusalMessage(err))")).toEqual([])
    expect(offendingLines("console.error('failed', err instanceof ApiError ? err.message : err)")).toEqual([])
    expect(offendingLines('// most screens show `err.message` as it is')).toEqual([])
  })
})
