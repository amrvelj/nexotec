import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Loader } from '@mantine/core'
import { Copy, Handshake } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { SalesDocumentsSection } from '../components/SalesDocumentsSection'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
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
  // KAN-266 — the create carries the mount's key, and the mount sends it
  // once: StrictMode runs this effect twice in development, and a second
  // POST made a second empty offer (and, under the same key, would be a 409
  // while the first is in flight, leaving this page on its loader).
  const idempotency = useIdempotencyKey()
  const created = useRef<Promise<SalesOfferRead> | null>(null)
  useEffect(() => {
    let cancelled = false
    created.current ??= api.post<SalesOfferRead>('/sales/offers', undefined, idempotency.headers())
    void created.current.then((offer) => {
      if (!cancelled) navigate(`/sales/offers/${offer.id}`, { replace: true })
    })
    return () => {
      cancelled = true
    }
  }, [navigate, idempotency])
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
  const navigate = useNavigate()
  const [copying, setCopying] = useState(false)
  // KAN-266 — a copy retried after a lost response replays the first copy;
  // a successful copy renews the key (this screen stays mounted when it
  // navigates to the copy).
  const copyKey = useIdempotencyKey()

  const offerQuery = useQuery({
    queryKey: ['sales-offer', id],
    queryFn: () => api.get<SalesOfferRead>(`/sales/offers/${id}`),
    enabled: Boolean(id),
  })

  // KAN-12 / PRD-Sales v2's own lifecycle table: "Pending Offer -> ...
  // Copy Offer (new offerId, prefilled)". This detail shell had NO
  // actions at all before this fix — Copy Offer is the one this ticket
  // asked for; Generate Contract/Cancel Offer (the same lifecycle row)
  // are a separate, unticketed gap, not built here.
  const copyOffer = async () => {
    setCopying(true)
    try {
      const path = `/sales/offers/${id}/copy`
      const copy = await api.post<SalesOfferRead>(path, undefined, copyKey.headers([path]))
      copyKey.renew()
      navigate(`/sales/offers/${copy.id}`)
    } finally {
      setCopying(false)
    }
  }

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
        primaryAction={{
          label: t('offerDetail.actions.copy'),
          icon: <Copy size={16} />,
          onClick: () => void copyOffer(),
          disabled: copying,
        }}
      />

      <SalesDocumentsSection ownerType="offer" ownerId={id} />
    </div>
  )
}
