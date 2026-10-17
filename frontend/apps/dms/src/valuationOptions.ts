import type { ValuationStatus } from '@nexotec/ui-kit'

type Translate = (key: string) => string

// The derived valuation status (draft | valid | expired | used) in the UI
// language — ValuationStatusBadge carries a tone only (KAN-334).
export function translatedValuationStatusLabel(t: Translate, status: ValuationStatus): string {
  return t(`valuationEnums.status.${status}`)
}
