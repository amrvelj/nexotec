import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useOverlay } from '@nexotec/ui-kit'
import { ConfiguratorOverlay } from '../configurator/ConfiguratorOverlay'
import type { ConfigurationRead } from '../../api/types'

/**
 * KAN-510 (Anto, 2026-11-01, option A) — «Konfiguration bearbeiten» on the
 * stock detail: the car's configuration opens in the configurator, in its
 * own mode only (the host / mode matrix: the configuration's mode, no mode
 * switch), as the offer reopens its configuration — `existing`, phase 2.
 * The VIN is corrected there like every other field, and the configuration's
 * own PATCH records it in its history. Stock writes nothing: the
 * configuration PATCH is the only write, and the answer replaces the cached
 * configuration every reader of `['configuration', id]` shows (the card, the
 * purchase dialog's VIN prefill).
 *
 * `onClosed` fires once when the overlay closes, committed or not (the
 * overlay stack's own `onClose`) — the purchase dialog uses it to come back.
 */
export function useStockConfigurationEditor(): (configuration: ConfigurationRead, onClosed?: () => void) => void {
  const overlay = useOverlay()
  const queryClient = useQueryClient()
  const { push, pop } = overlay

  return useCallback(
    (configuration: ConfigurationRead, onClosed?: () => void) =>
      push({
        key: `configurator-edit-stock-configuration-${configuration.id}`,
        onClose: onClosed,
        content: (
          <ConfiguratorOverlay
            allowedModes={[configuration.mode]}
            existing={configuration}
            onCommitted={(saved) => {
              queryClient.setQueryData(['configuration', saved.id], saved)
              pop()
            }}
            onClose={() => pop()}
          />
        ),
      }),
    [push, pop, queryClient],
  )
}
