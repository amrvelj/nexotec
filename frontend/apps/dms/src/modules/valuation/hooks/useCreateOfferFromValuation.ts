import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../../../api/client'
import type { SalesOfferRead, ValuationRead } from '../../../api/types'
import { apiRefusalText } from '../../../utils/apiRefusal'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'

export interface CreateOfferFromValuationError {
  /** Built from `t(...)` in the UI language — never an API's `message`. */
  text: string
  /** The offer this action created when only a later step failed: it
   * exists, so the screen offers to open it. Null when no offer was made. */
  offer: Pick<SalesOfferRead, 'id' | 'offerNumber'> | null
}

/**
 * KAN-569 (ADR-061; Anto's answer A on KAN-528, then 1A and 2A on KAN-569) —
 * «Offerte aus dieser Bewertung erstellen», shared by the valuation detail
 * and the valuations grid's row menu. Three calls, the ones the SPA already
 * makes elsewhere:
 *
 * 1. `POST /sales/offers` — a bare draft offer (the endpoint takes no body);
 * 2. `PATCH /sales/offers/{id}` with the valuation's `customerId`, only when
 *    it has one (1A: without a customer the offer starts without a buyer, as
 *    an offer from a stock car does, and the seller picks one in the
 *    workspace);
 * 3. `POST /sales/offers/{id}/trade-in/valuation` — the workspace's own
 *    record-mode trade-in path (KAN-10/KAN-67);
 *
 * then it opens the offer workspace. A refusal is said, never dropped
 * (ADR-035). When the offer was created but step 2 or 3 was refused, the
 * screen stays where the user acted, says which offer exists and offers to
 * open it; a retry continues that offer (re-read, then only the steps still
 * missing), so no second offer appears. Each POST carries an
 * Idempotency-Key that a retry keeps (KAN-119).
 */
export function useCreateOfferFromValuation(): {
  run: (valuation: Pick<ValuationRead, 'id' | 'customerId'>) => Promise<void>
  error: CreateOfferFromValuationError | null
  dismiss: () => void
} {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const createKey = useIdempotencyKey()
  const attachKey = useIdempotencyKey()
  const [error, setError] = useState<CreateOfferFromValuationError | null>(null)
  // The offer a failed run created, per valuation: the retry continues it.
  const pending = useRef<{ valuationId: string; offerId: string } | null>(null)
  // A second click while a run is in flight starts nothing (the menu entry
  // stays enabled: a disabled item without a reason is a defect, ADR-061).
  const inFlight = useRef(false)

  const run = useCallback(
    async (valuation: Pick<ValuationRead, 'id' | 'customerId'>) => {
      if (inFlight.current) return
      inFlight.current = true
      setError(null)
      let offer: SalesOfferRead
      try {
        offer =
          pending.current?.valuationId === valuation.id
            ? await api.get<SalesOfferRead>(`/sales/offers/${pending.current.offerId}`)
            : await api.post<SalesOfferRead>('/sales/offers', undefined, createKey.headers([valuation.id]))
      } catch (err) {
        setError({ text: apiRefusalText(t, err, 'valuationDetail.errors.createOfferFailed'), offer: null })
        inFlight.current = false
        return
      }
      try {
        if (valuation.customerId && offer.customerId !== valuation.customerId) {
          offer = await api.patch<SalesOfferRead>(
            `/sales/offers/${offer.id}`,
            { customerId: valuation.customerId },
            { 'If-Match': String(offer.version) }
          )
        }
        if (offer.tradeInValuationId !== valuation.id) {
          const path = `/sales/offers/${offer.id}/trade-in/valuation`
          const body = { valuationId: valuation.id }
          offer = await api.post<SalesOfferRead>(path, body, {
            'If-Match': String(offer.version),
            ...attachKey.headers([path, body]),
          })
        }
        pending.current = null
        createKey.renew()
        attachKey.renew()
        navigate(`/sales/offers/${offer.id}`)
      } catch (err) {
        pending.current = { valuationId: valuation.id, offerId: offer.id }
        setError({
          text: t('valuationDetail.errors.createOfferTradeInFailed', {
            offerNumber: offer.offerNumber,
            reason: apiRefusalText(t, err, 'valuationDetail.errors.createOfferFailed'),
          }),
          offer: { id: offer.id, offerNumber: offer.offerNumber },
        })
      } finally {
        inFlight.current = false
      }
    },
    [attachKey, createKey, navigate, t]
  )

  const dismiss = useCallback(() => setError(null), [])
  return { run, error, dismiss }
}
