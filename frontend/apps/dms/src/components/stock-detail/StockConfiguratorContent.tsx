import { useCallback, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { ConfiguratorOverlay, type HostOwnedVin } from '../configurator/ConfiguratorOverlay'
import { HostCommitError } from '../configurator/hostCommitError'
import type { ConfigurationRead, StockItemRead } from '../../api/types'
import { VinCorrectionDialog } from './VinCorrectionDialog'

interface PendingCorrection {
  vin: string
  item: StockItemRead
  resolve: () => void
  reject: (err: unknown) => void
}

/**
 * KAN-557 — the stock car's configurator (`useStockConfigurationEditor`): on
 * a car with a vehicle-mdm record it owns the VIN (`hostOwnsVin`) and answers
 * a changed one with «VIN korrigieren», prefilled; the dialog's success lets
 * the overlay commit, its cancel keeps the overlay open.
 */
export function StockConfiguratorContent({
  configuration,
  item,
  canCorrectVin,
  onCommitted,
  onClose,
}: {
  configuration: ConfigurationRead
  /** Set only for a car with a vehicle-mdm record: then Stock owns the VIN. */
  item: StockItemRead | undefined
  canCorrectVin: boolean
  onCommitted: (saved: ConfigurationRead) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<PendingCorrection | null>(null)
  const pendingRef = useRef<PendingCorrection | null>(null)

  const settle = useCallback((outcome: 'corrected' | 'cancelled') => {
    const current = pendingRef.current
    if (!current) return
    pendingRef.current = null
    setPending(null)
    if (outcome === 'corrected') current.resolve()
    else current.reject(new HostCommitError(t('stockDetail.vinCorrection.notCorrected')))
  }, [t])

  const hostOwnsVin: HostOwnedVin | undefined = useMemo(() => {
    if (!item) return undefined
    return {
      hint: t('stockDetail.vinCorrection.configuratorHint'),
      clearRefused: t('stockDetail.vinCorrection.configuratorClearRefused'),
      readOnlyReason: canCorrectVin ? undefined : t('stockDetail.vinCorrection.configuratorReadOnly'),
      correctVin: async (vin: string) => {
        // The configuration's own saves just ran: the dialog sends the stock
        // item's version as the server holds it now.
        const fresh = await queryClient.fetchQuery({
          queryKey: ['stock-item', item.id],
          queryFn: () => api.get<StockItemRead>(`/inventory/stock-items/${item.id}`),
          staleTime: 0,
        })
        return new Promise<void>((resolve, reject) => {
          const next = { vin, item: fresh, resolve, reject }
          pendingRef.current = next
          setPending(next)
        })
      },
    }
  }, [item, canCorrectVin, t, queryClient])

  return (
    <>
      <ConfiguratorOverlay
        allowedModes={[configuration.mode]}
        existing={configuration}
        hostOwnsVin={hostOwnsVin}
        onCommitted={onCommitted}
        onClose={onClose}
      />
      {pending && (
        <VinCorrectionDialog
          opened
          item={pending.item}
          initialVin={pending.vin}
          onCorrected={() => settle('corrected')}
          onClose={() => settle('cancelled')}
        />
      )}
    </>
  )
}
