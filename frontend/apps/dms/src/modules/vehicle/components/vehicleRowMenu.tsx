import { ClipboardCheck, Copy, GitMerge, Gauge, Package, RectangleHorizontal, RefreshCw, UserPlus } from 'lucide-react'
import type { DetailHeaderAction, RowMenuGroups } from '@nexotec/ui-kit'

/**
 * FR-V-16 (amended by KAN-468; Anto, 2026-10-26) — the ONE definition of
 * what can be done to a vehicle (ADR-061): the Vehicle 360 header renders
 * its primary, alternative and overflow from it, the way
 * `buildCustomerRowMenu` serves the customer screens.
 *
 * There is no «Edit»: the identity fields are edited inline on the Identity
 * tab (FR-V-15), so an Edit button could only select that tab. The primary
 * is «Allocate to customer» (FR-V-05), the alternative «Request valuation»
 * (FR-V-09). A valuation is a Sales-module surface (KAN-198, D-A-04), so a
 * Betrieb without `sales` gets neither the alternative nor its overflow
 * entry — presentation only, the API's 404 stays the boundary.
 */

export interface VehicleRowMenuHandlers {
  onAllocate: () => void
  onRecordOdometer: () => void
  onRecordAccessory: () => void
  /** KAN-467 — «Kontrollschild erfassen» (FR-V-16's overflow). */
  onRecordPlate: () => void
  onCopyVin: () => void
  /** Passed only when the active Betrieb holds `sales`. */
  onRequestValuation?: () => void
  /**
   * KAN-524 — «Zusammenführen mit…» (FR-V-12). Passed only when the user may
   * edit vehicles (`vehicle_mdm`); without it the entry is left out — an action
   * the role may never use is hidden (KAN-430), unlike one the state refuses.
   */
  onMerge?: () => void
}

/**
 * KAN-503 — the row-menu actions that need Vehicle 360's own state (the
 * Identity tab's customer picker, the odometer and accessory forms). The
 * Vehicles list opens the vehicle with one of these in the router state
 * (`vehicleOpenState`); the detail screen runs the same handler as its
 * header, then drops the state, so a reload or Back never repeats it.
 */
export type VehicleOpenAction = 'allocate' | 'recordOdometer' | 'recordAccessory' | 'recordPlate' | 'merge'

const OPEN_ACTIONS: readonly VehicleOpenAction[] = ['allocate', 'recordOdometer', 'recordAccessory', 'recordPlate', 'merge']

export function vehicleOpenState(action: VehicleOpenAction): { vehicleOpenAction: VehicleOpenAction } {
  return { vehicleOpenAction: action }
}

/** The action a navigation asked for, or null for any other state. */
export function readVehicleOpenAction(state: unknown): VehicleOpenAction | null {
  const action = (state as { vehicleOpenAction?: unknown } | null)?.vehicleOpenAction
  return OPEN_ACTIONS.find((known) => known === action) ?? null
}

export interface VehicleRowMenu {
  primary: DetailHeaderAction
  alternative?: DetailHeaderAction
  overflow: RowMenuGroups
}

type T = (key: string, options?: Record<string, unknown>) => string

export function buildVehicleRowMenu(t: T, handlers: VehicleRowMenuHandlers): VehicleRowMenu {
  const allocate = {
    label: t('vehicleDetail.header.allocate'),
    icon: <UserPlus size={16} />,
    onClick: handlers.onAllocate,
  }
  const requestValuation = handlers.onRequestValuation && {
    label: t('vehicleDetail.header.requestValuation'),
    icon: <ClipboardCheck size={16} />,
    onClick: handlers.onRequestValuation,
  }

  const edit: RowMenuGroups['edit'] = [
    allocate,
    { label: t('vehicleDetail.header.recordOdometer'), icon: <Gauge size={16} />, onClick: handlers.onRecordOdometer },
    { label: t('vehicleDetail.header.recordAccessory'), icon: <Package size={16} />, onClick: handlers.onRecordAccessory },
    { label: t('vehicleDetail.header.recordPlate'), icon: <RectangleHorizontal size={16} />, onClick: handlers.onRecordPlate },
    {
      // FR-V-02's re-sync of a vehicle from the catalogue is not built:
      // shown and explained, never hidden (ADR-061).
      label: t('vehicleDetail.header.resync'),
      icon: <RefreshCw size={16} />,
      onClick: () => {},
      disabled: true,
      disabledReason: t('vehicleDetail.header.resyncDisabledReason'),
    },
  ]

  return {
    primary: allocate,
    alternative: requestValuation,
    overflow: {
      edit,
      createFrom: requestValuation ? [requestValuation] : [],
      exportPrint: [{ label: t('vehicleDetail.header.copyVin'), icon: <Copy size={16} />, onClick: handlers.onCopyVin }],
      destructive: [
        ...(handlers.onMerge
          ? [{ label: t('vehicleDetail.header.merge'), icon: <GitMerge size={16} />, onClick: handlers.onMerge }]
          : []),
      ],
    },
  }
}
