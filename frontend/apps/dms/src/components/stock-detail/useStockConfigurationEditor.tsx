import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useOverlay } from '@nexotec/ui-kit'
import type { ConfigurationRead, StockItemRead } from '../../api/types'
import { StockConfiguratorContent } from './StockConfiguratorContent'

/**
 * KAN-510 (Anto, 2026-11-01, option A) — «Konfiguration bearbeiten» on the
 * stock detail: the car's configuration opens in the configurator, in its
 * own mode only (the host / mode matrix: the configuration's mode, no mode
 * switch), as the offer reopens its configuration — `existing`, phase 2.
 * Stock writes nothing of the configuration: its PATCH is the only write
 * there, and the answer replaces the cached configuration every reader of
 * `['configuration', id]` shows (the card, the purchase dialog's VIN prefill).
 *
 * KAN-557 — the VIN of a car with a vehicle-mdm record (bought, or stornoed
 * keeping it) is Stock's «VIN korrigieren», never the configuration PATCH:
 * the overlay leaves `vin` out and hands a changed one here, and
 * `VinCorrectionDialog` (prefilled with it) corrects the stock item, the
 * record and the configuration together. Its success lets the overlay
 * commit; a cancel keeps the overlay open with the other fields saved.
 * Without `stock_vin_correction` the VIN is read-only there, with the reason.
 * A pipeline car without a record keeps correcting its VIN in the
 * configuration (it wins at booking, KAN-575).
 *
 * `onClosed` fires once when the overlay closes, committed or not (the
 * overlay stack's own `onClose`) — the purchase dialog uses it to come back.
 */
export function useStockConfigurationEditor(
  /** The stock car whose configuration this is; without one, the VIN is the
   * configuration's as before. */
  item?: StockItemRead,
  /** `stock_vin_correction` — the dealer manager. */
  canCorrectVin = false,
): (configuration: ConfigurationRead, onClosed?: () => void) => void {
  const overlay = useOverlay()
  const queryClient = useQueryClient()
  const { push, pop } = overlay

  return useCallback(
    (configuration: ConfigurationRead, onClosed?: () => void) =>
      push({
        key: `configurator-edit-stock-configuration-${configuration.id}`,
        onClose: onClosed,
        content: (
          <StockConfiguratorContent
            configuration={configuration}
            item={item?.vehicleId ? item : undefined}
            canCorrectVin={canCorrectVin}
            onCommitted={(saved) => {
              queryClient.setQueryData(['configuration', saved.id], saved)
              pop()
            }}
            onClose={() => pop()}
          />
        ),
      }),
    [push, pop, queryClient, item, canCorrectVin],
  )
}
