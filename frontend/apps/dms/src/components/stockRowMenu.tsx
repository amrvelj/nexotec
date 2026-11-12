import { FileSignature, FingerprintPattern, Handshake, PackagePlus, RefreshCcw, Undo2, Workflow } from 'lucide-react'
import type { DetailHeaderAction, RowMenuAction, RowMenuGroups } from '@nexotec/ui-kit'
import type { StockItemRead } from '../api/types'

/**
 * KAN-571 (ADR-061; PRD-Stock FR-I-07: the detail header carries "the same
 * action set as the stock overview row menu") — the ONE definition of what
 * can be done to a stock car. The Stock list's ⋯ menu and the stock detail's
 * header (primary, alternative, overflow) both render from it, the way
 * `buildVehicleRowMenu` serves the vehicle screens (KAN-503).
 *
 * - Primary «Vertrag erstellen», alternative «Offerte erstellen» (KAN-307).
 * - «Einkauf erfassen» and «Einkauf stornieren» are both always there (Anto,
 *   2026-11-07, KAN-571 2a option A): the one that does not apply is
 *   disabled with its reason, so the menu reads the same on every car.
 * - «Zustand ändern» (Anto, 2b option A) opens its own dialog.
 * - «Konfiguration bearbeiten» on a car with a configuration, «Konfiguration
 *   zuordnen» on one without (KAN-463, KAN-510).
 * - «VIN korrigieren» (KAN-557, ADR-116) for the dealer manager
 *   (`stock_vin_correction`): the stock item, its vehicle-mdm record and its
 *   configuration together. Disabled on a car that left stock, and on one
 *   without a vehicle-mdm record (never bought) — that car's VIN is
 *   corrected in its configuration.
 *
 * What the user's role may never use is hidden (KAN-430) — «Vertrag
 * erstellen» and «Offerte erstellen» too (`sales_contracts`, `sales_offers`),
 * header buttons included; what this car
 * does not allow now is disabled with the reason (a car that left stock, a
 * car a contract reserves, a purchase booked or not).
 */

export type StockOpenAction =
  | 'createContract'
  | 'createOffer'
  | 'recordPurchase'
  | 'stornoPurchase'
  | 'changeCondition'
  | 'attachConfiguration'
  | 'editConfiguration'
  | 'correctVin'

const OPEN_ACTIONS: readonly StockOpenAction[] = [
  'createContract',
  'createOffer',
  'recordPurchase',
  'stornoPurchase',
  'changeCondition',
  'attachConfiguration',
  'editConfiguration',
  'correctVin',
]

/** The Stock list opens the car with the action picked there in the router
 * state (never the URL); the detail runs it once, as its header would. */
export function stockOpenState(action: StockOpenAction): { stockOpenAction: StockOpenAction } {
  return { stockOpenAction: action }
}

/** The action a navigation asked for, or null for any other state. */
export function readStockOpenAction(state: unknown): StockOpenAction | null {
  const action = (state as { stockOpenAction?: unknown } | null)?.stockOpenAction
  return OPEN_ACTIONS.find((known) => known === action) ?? null
}

export type StockRowMenuItem = Pick<
  StockItemRead,
  'purchasePrice' | 'leftStockAt' | 'reservationState' | 'configurationId' | 'vehicleId'
>

export interface StockRowMenuPermissions {
  /** `sales_contracts`. */
  canCreateContract: boolean
  /** `sales_offers`. */
  canCreateOffer: boolean
  /** `stock_acquisition` — books and stornos the purchase. */
  canBookPurchase: boolean
  /** `stock_condition`. */
  canChangeCondition: boolean
  /** `stock_items` and `configurations`. */
  canAttachConfiguration: boolean
  /** `configurations`. */
  canEditConfiguration: boolean
  /** `stock_vin_correction` — the dealer manager only (KAN-557). */
  canCorrectVin: boolean
}

export type StockRowMenuHandlers = Record<StockOpenAction, () => void>

export interface StockRowMenu {
  /** Absent for a role that may not create contracts. */
  primary?: DetailHeaderAction
  /** Absent for a role that may not create offers. */
  alternative?: DetailHeaderAction
  overflow: RowMenuGroups
  /** Each action the menu offers, by name — absent when hidden for the role.
   * A detail opened from the list runs the asked-for action only when it is
   * offered here and not disabled. */
  offered: Partial<Record<StockOpenAction, RowMenuAction>>
}

type T = (key: string, options?: Record<string, unknown>) => string

export function buildStockRowMenu(
  t: T,
  item: StockRowMenuItem,
  handlers: StockRowMenuHandlers,
  permissions: StockRowMenuPermissions,
  /** The date the car left stock, already formatted in the user's locale. */
  leftStockDate: string | null,
  /** The detail is creating an offer or a contract for this car: both deal
   * actions wait for it, and say so (ADR-061: never disabled without a reason). */
  creatingDeal = false,
): StockRowMenu {
  const leftStockReason = leftStockDate ? t('stockDetail.actions.leftStock', { date: leftStockDate }) : undefined
  const left = leftStockReason !== undefined
  const booked = item.purchasePrice != null
  const dealReason = leftStockReason ?? (creatingDeal ? t('stockDetail.rowMenu.creatingDeal') : undefined)

  const contract: RowMenuAction = {
    label: t('stockDetail.actions.createContract'),
    icon: <FileSignature size={16} />,
    onClick: handlers.createContract,
    disabled: dealReason !== undefined,
    disabledReason: dealReason,
  }
  const offer: RowMenuAction = {
    label: t('stockDetail.actions.createOffer'),
    icon: <Handshake size={16} />,
    onClick: handlers.createOffer,
    disabled: dealReason !== undefined,
    disabledReason: dealReason,
  }

  const offered: Partial<Record<StockOpenAction, RowMenuAction>> = {}
  const register = (name: StockOpenAction, action: RowMenuAction): RowMenuAction => {
    offered[name] = action
    return action
  }

  const edit: NonNullable<RowMenuGroups['edit']> = []
  if (permissions.canBookPurchase) {
    const reason = leftStockReason ?? (booked ? t('stockDetail.rowMenu.purchaseAlreadyBooked') : undefined)
    edit.push(register('recordPurchase', {
      label: t('stockDetail.purchase.recordButton'),
      icon: <PackagePlus size={16} />,
      onClick: handlers.recordPurchase,
      disabled: reason !== undefined,
      disabledReason: reason,
    }))
  }
  if (permissions.canChangeCondition) {
    edit.push(register('changeCondition', {
      label: t('stockDetail.rowMenu.changeCondition'),
      icon: <RefreshCcw size={16} />,
      onClick: handlers.changeCondition,
      disabled: left,
      disabledReason: leftStockReason,
    }))
  }
  if (item.configurationId) {
    if (permissions.canEditConfiguration) {
      // KAN-510: a car a contract reserves is changed through that contract.
      const reason = leftStockReason ?? (item.reservationState === 'reserved' ? t('stockDetail.configuration.reservedByContract') : undefined)
      edit.push(register('editConfiguration', {
        label: t('stockDetail.configuration.edit'),
        icon: <Workflow size={16} />,
        onClick: handlers.editConfiguration,
        disabled: reason !== undefined,
        disabledReason: reason,
      }))
    }
  } else if (permissions.canAttachConfiguration) {
    edit.push(register('attachConfiguration', {
      label: t('stockDetail.configuration.attach'),
      icon: <Workflow size={16} />,
      onClick: handlers.attachConfiguration,
    }))
  }

  if (permissions.canCorrectVin) {
    const reason = leftStockReason ?? (item.vehicleId == null ? t('stockDetail.vinCorrection.needsPurchase') : undefined)
    edit.push(register('correctVin', {
      label: t('stockDetail.vinCorrection.action'),
      icon: <FingerprintPattern size={16} />,
      onClick: handlers.correctVin,
      disabled: reason !== undefined,
      disabledReason: reason,
    }))
  }

  const destructive: NonNullable<RowMenuGroups['destructive']> = []
  if (permissions.canBookPurchase) {
    const reason = left
      ? t('stockDetail.purchase.storno.leftStock')
      : booked
        ? undefined
        : t('stockDetail.rowMenu.purchaseNotBooked')
    destructive.push(register('stornoPurchase', {
      label: t('stockDetail.purchase.storno.button'),
      icon: <Undo2 size={16} />,
      onClick: handlers.stornoPurchase,
      disabled: reason !== undefined,
      disabledReason: reason,
    }))
  }

  const createFrom: RowMenuAction[] = []
  if (permissions.canCreateContract) createFrom.push(register('createContract', contract))
  if (permissions.canCreateOffer) createFrom.push(register('createOffer', offer))

  return {
    primary: permissions.canCreateContract ? contract : undefined,
    alternative: permissions.canCreateOffer ? offer : undefined,
    overflow: { edit, createFrom, destructive },
    offered,
  }
}
