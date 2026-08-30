import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Loader } from '@mantine/core'
import { Handshake } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { OfferWorkspaceContent } from './OfferWorkspacePage'
import type { SalesOfferRead } from '../api/types'

/** `/sales/offers/new` — the confirmed reference prototype allocates a
 * number and opens an empty draft before anything is chosen; this route
 * does exactly that (POST, then redirect to the real id) rather than
 * rendering a separate "new" form. The container-based generation
 * workspace itself (Kunde/Fahrzeug/Preisaufbau/Eintauschfahrzeug/Leasing)
 * is PR-2 — this page is the minimal PR-1 shell that makes an offer
 * viewable at all.
 */
export function OfferCreateRedirectPage() {
  const navigate = useNavigate()
  useEffect(() => {
    let cancelled = false
    void api.post<SalesOfferRead>('/sales/offers').then((offer) => {
      if (!cancelled) navigate(`/sales/offers/${offer.id}`, { replace: true })
    })
    return () => {
      cancelled = true
    }
  }, [navigate])
  return <Loader />
}

export function OfferDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <OfferDetailContent offerId={id} />
}

export interface OfferDetailContentProps {
  offerId: string
  /** § ADR-059 — true when rendered as an Overlay rather than the real
   * `/sales/offers/:id` route. */
  embedded?: boolean
}

/** PR-1's minimal offer detail — header + business key + status only.
 * Containers (PR-2), pricing (PR-3), trade-in (PR-5), the two-step
 * generation/review flow (PR-8) all extend this same shell.
 */
export function OfferDetailContent({ offerId: id, embedded = false }: OfferDetailContentProps) {
  const { t } = useTranslation()

  const offerQuery = useQuery({
    queryKey: ['sales-offer', id],
    queryFn: () => api.get<SalesOfferRead>(`/sales/offers/${id}`),
    enabled: Boolean(id),
  })

  useSetBreadcrumb(embedded ? null : [t('shell.nav.sales'), offerQuery.data?.offerNumber ?? id])

  if (offerQuery.isLoading) return <Loader />
  if (offerQuery.isError || !offerQuery.data) {
    return (
      <Alert color="red" title={t('offerDetail.errors.failedToLoad')}>
        {offerQuery.error instanceof ApiError ? offerQuery.error.message : t('offerDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const offer = offerQuery.data

  // While a draft, the container workspace (PR-2) IS the detail screen —
  // there is nothing else to show yet. Once it leaves draft (PR-6), this
  // minimal header shell takes over; PR-8's two-step generation/review
  // extends it further.
  if (offer.status === 'draft' && !embedded) {
    return <OfferWorkspaceContent offerId={id} />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        entityMark={<Handshake size={24} />}
        title={offer.vehicleLabel ?? t('offerDetail.untitled')}
        businessKey={offer.offerNumber}
        badges={<></>}
      />
    </div>
  )
}
