import { Car, CheckCircle, Handshake, RefreshCw, Undo2, User } from 'lucide-react'
import type { DetailHeaderAction, RowMenuAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { ValuationRead } from '../api/types'
import { normalisedVin } from '../utils/vin'

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
  /** KAN-569 (ADR-061; Anto's answer A on KAN-528) — «Offerte aus dieser
   * Bewertung erstellen». Passed only when the session may write offers
   * (`sales_offers`); otherwise the entry is hidden, never disabled (KAN-430).
   * `salesEntitled` is the active Betrieb's `sales` module (KAN-377): without
   * it the entry is shown disabled with its reason. A second click while a
   * run is in flight is ignored by the run itself
   * (`useCreateOfferFromValuation`), never by a reason-less disabled item. */
  createOffer?: { onClick: () => void; salesEntitled: boolean }
}

export interface ValuationRowMenu {
  primary: DetailHeaderAction
  /** «Offerte aus dieser Bewertung erstellen» beside «Neu bewerten», when
   * the session may write offers (KAN-569); also in the overflow's
   * `createFrom` group, so the grid's row menu carries the same entry. */
  alternative?: DetailHeaderAction
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
 *
 * «Offerte aus dieser Bewertung erstellen» (KAN-569) starts a draft offer
 * with this valuation as its trade-in. The API attaches an expired or used
 * valuation without complaint and refuses an expired one only at contract
 * confirmation (`trade_in_valuation_expired`), so the menu says so before the
 * click. A draft is allowed and a valuation without a customer gives an offer
 * without a buyer (Anto, 2026-11-05, answers 1A and 2A): the action is no
 * stricter than the contract that will consume the valuation.
 */
export function buildValuationRowMenu(
  t: (key: string, options?: Record<string, unknown>) => string,
  valuation: Pick<ValuationRead, 'status' | 'customerId' | 'vehicleId' | 'hasSignedContract' | 'vehicleVin'> &
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

  const createOfferDisabledReason = !actions.createOffer?.salesEntitled
    ? t('valuationDetail.actions.createOfferNoSales')
    : valuation.status === 'expired'
      ? t('valuationDetail.actions.createOfferExpired')
      : valuation.status === 'used'
        ? t('valuationDetail.actions.createOfferUsed')
        : // KAN-574 (Anto, 2026-11-07): a trade-in is a car. A valuation
          // written before VIN became required is refused as a trade-in, and
          // so is one carrying a pre-1981 chassis number until KAN-535 (open
          // with Anto on KAN-574) — the API refuses both
          // (`trade_in_valuation_names_no_car`), so the action says why first.
          normalisedVin(valuation.vehicleVin ?? '') === null
          ? t('valuationDetail.actions.createOfferNoVin')
          : undefined
  const createOffer: RowMenuAction | undefined = actions.createOffer && {
    label: t('valuationDetail.actions.createOffer'),
    icon: <Handshake size={16} />,
    onClick: actions.createOffer.onClick,
    disabled: createOfferDisabledReason !== undefined,
    disabledReason: createOfferDisabledReason,
  }

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
    ...(createOffer ? { createFrom: [createOffer] } : {}),
  }

  return {
    primary: {
      label: t('valuationDetail.actions.revalue'),
      icon: <RefreshCw size={16} />,
      onClick: actions.onRevalue,
    },
    alternative: createOffer,
    overflow,
  }
}
