import type { TFunction } from 'i18next'
import type { ConfirmedWarningWrite, WarningCode } from '../../api/types'

/** KAN-809 — a warning of the one write path (planning.md) as one sentence,
 * naming the resource it was raised for. */
export function warningText(
  t: TFunction,
  warning: { code: WarningCode; resourceId?: string | null },
  resourceLabels: ReadonlyMap<string, string>,
): string {
  const resource = (warning.resourceId && resourceLabels.get(warning.resourceId)) || '—'
  return t(`planner.warnings.${warning.code}`, { resource })
}

/** The warnings a 422 `planning_warnings_unconfirmed` listed, or null for any other error body. */
export function unconfirmedWarnings(details: Record<string, unknown> | null | undefined): ConfirmedWarningWrite[] | null {
  if (!details || details.reason !== 'planning_warnings_unconfirmed' || !Array.isArray(details.warnings)) return null
  return (details.warnings as ConfirmedWarningWrite[]).map((w) => ({
    code: w.code,
    resourceId: w.resourceId ?? null,
    againstBookingId: w.againstBookingId ?? null,
  }))
}
