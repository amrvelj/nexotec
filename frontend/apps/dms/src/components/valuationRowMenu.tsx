import { Car, CheckCircle, RefreshCw, Undo2, User } from 'lucide-react'
import type { DetailHeaderAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { ValuationRead } from '../api/types'

/** KAN-253 — the server's `CONFIRMATION_GRACE`: a stamp younger than this may
 * be a confirmation still signing its contract, so it is not cleared (409
 * `stamp_too_recent`); the menu says so before the click. */
const CONFIRMATION_GRACE_MS = 60 * 60 * 1000

export interface ValuationRowMenuActions {
  onRevalue: () => void
  onMarkUsed: () => void
  /** KAN-253 — «Verwendung aufheben». Passed only when the session may
   * write `valuation_use_reversal` (the dealer manager); otherwise the
   * entry is hidden, never disabled (KAN-430). */
  onRevertUse?: () => void
  onOpenCustomer?: () => void
  onOpenVehicle?: () => void
}

export interface ValuationRowMenu {
  primary: DetailHeaderAction
  overflow: RowMenuGroups
}

/**
 * WP-8 PR-9 (ADR-061 anti-drift) — the ONE function both
 * ValuationDetailPage's header and ValuationsListPage's own grid row menu
 * call, so a disabled action can never carry a different reason (or a
 * different enabled state) on the two surfaces. There is no "Bearbeiten"
 * action anywhere: a Valuation is immutable once created (ADR-066's own
 * model docstring, app/valuation/models/valuation.py) — "Neu bewerten"
 * (create a new row with `supersedesValuationId` set) is the only
 * correction mechanism, for ANY status including draft.
 *
 * «Als verwendet markieren» repairs a stamp a signed deal should have set:
 * only a signed deal uses a valuation (KAN-115), so it is offered only when a
 * signed contract carries the valuation — whatever its status, expired
 * included — and never once it is already used.
 *
 * «Verwendung aufheben» (KAN-253) is its counterpart for a stamp no signed
 * deal stands behind — a hand stamp from before KAN-115, or a confirmation
 * that failed and was cancelled: offered to the dealer manager only, enabled
 * only for a used valuation no signed contract carries.
 */
export function buildValuationRowMenu(
  t: (key: string, options?: Record<string, unknown>) => string,
  valuation: Pick<ValuationRead, 'status' | 'customerId' | 'vehicleId' | 'hasSignedContract'> &
    Partial<Pick<ValuationRead, 'usedAt'>>,
  actions: ValuationRowMenuActions
): ValuationRowMenu {
  const markUsedDisabledReason =
    valuation.status === 'used'
      ? t('valuationDetail.actions.markUsedAlreadyUsed')
      : !valuation.hasSignedContract
        ? t('valuationDetail.actions.markUsedNoSignedContract')
        : undefined
  const revertUseDisabledReason =
    valuation.status !== 'used'
      ? t('valuationDetail.actions.revertUseNotUsed')
      : valuation.hasSignedContract
        ? t('valuationDetail.actions.revertUseSignedContract')
        : valuation.usedAt && Date.now() - Date.parse(valuation.usedAt) < CONFIRMATION_GRACE_MS
          ? t('valuationDetail.actions.revertUseTooRecent')
          : undefined

  const overflow: RowMenuGroups = {
    navigate: [
      ...(valuation.customerId && actions.onOpenCustomer
        ? [{ label: t('valuationDetail.actions.openCustomer'), icon: <User size={16} />, onClick: actions.onOpenCustomer }]
        : []),
      ...(valuation.vehicleId && actions.onOpenVehicle
        ? [{ label: t('valuationDetail.actions.openVehicle'), icon: <Car size={16} />, onClick: actions.onOpenVehicle }]
        : []),
    ],
    edit: [
      {
        label: t('valuationDetail.actions.markUsed'),
        icon: <CheckCircle size={16} />,
        onClick: actions.onMarkUsed,
        disabled: markUsedDisabledReason !== undefined,
        disabledReason: markUsedDisabledReason,
      },
      ...(actions.onRevertUse
        ? [
            {
              label: t('valuationDetail.actions.revertUse'),
              icon: <Undo2 size={16} />,
              onClick: actions.onRevertUse,
              disabled: revertUseDisabledReason !== undefined,
              disabledReason: revertUseDisabledReason,
            },
          ]
        : []),
    ],
  }

  return {
    primary: {
      label: t('valuationDetail.actions.revalue'),
      icon: <RefreshCw size={16} />,
      onClick: actions.onRevalue,
    },
    overflow,
  }
}
