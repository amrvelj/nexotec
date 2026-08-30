import { Car, CheckCircle, RefreshCw, User } from 'lucide-react'
import type { DetailHeaderAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { ValuationRead } from '../api/types'

export interface ValuationRowMenuActions {
  onRevalue: () => void
  onMarkUsed: () => void
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
 */
export function buildValuationRowMenu(
  t: (key: string, options?: Record<string, unknown>) => string,
  valuation: Pick<ValuationRead, 'status' | 'customerId' | 'vehicleId'>,
  actions: ValuationRowMenuActions
): ValuationRowMenu {
  const canMarkUsed = valuation.status === 'valid' || valuation.status === 'draft'

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
        disabled: !canMarkUsed,
        disabledReason: !canMarkUsed ? t('valuationDetail.actions.markUsedDisabledReason') : undefined,
      },
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
