import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Loader, NumberInput, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { OverviewCard, StickyActionFooter, useOverlay, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { OfferGenerateReviewModal } from '../components/OfferGenerateReviewModal'
import {
  DealCustomerContainer,
  DealPricingContainer,
  DealTradeInContainer,
  DealVehicleContainer,
  RequirementBadge,
  useDealCustomer,
} from '../components/deal-workspace/DealWorkspaceContainers'
import { useDebouncedNumberField } from '../hooks/useDebouncedNumberField'
import { CustomerDetailContent } from './CustomerDetailPage'
import { StockDetailContent } from './StockDetailPage'
import type { SalesOfferRead } from '../api/types'

/**
 * `/sales/offers/:id` while status=draft — the container-based generation
 * workspace (FR-S-05, WP-8 PR-2). Fixed container order
 * Kunde -> Fahrzeug -> Preisaufbau -> Eintauschfahrzeug -> Leasing
 * (confirmed live); any order EXCEPT pricing, which needs a vehicle first.
 * Once the offer leaves draft, OfferDetailContent's own shell takes over
 * (App.tsx routes both to the same URL — this page renders only while
 * editable).
 */
export function OfferWorkspacePage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <OfferWorkspaceContent offerId={id} />
}

export function OfferWorkspaceContent({ offerId: id }: { offerId: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  useSetBreadcrumb([t('shell.nav.sales'), t('offerWorkspace.title')])

  const offerQuery = useQuery({
    queryKey: ['sales-offer', id],
    queryFn: () => api.get<SalesOfferRead>(`/sales/offers/${id}`),
    enabled: Boolean(id),
  })

  const overlay = useOverlay()
  const customerId = offerQuery.data?.customerId ?? null
  const selectedCustomer = useDealCustomer(customerId).data ?? null
  // § ADR-059 — opening the selected customer's own record from inside
  // the offer-generation process is an overlay, never a navigation (the
  // half-built offer must survive it intact).
  const openCustomerOverlay = () => {
    if (!customerId) return
    overlay.push({ key: `customer-overlay-${customerId}`, content: <CustomerDetailContent customerId={customerId} embedded /> })
  }
  // Same ADR-059 posture — KAN-64: the pricing section needs a way out to
  // the stock record when it turns out there is no price to build from.
  const stockItemId = offerQuery.data?.stockItemId ?? null
  const openStockItemOverlay = () => {
    if (!stockItemId) return
    overlay.push({ key: `stock-overlay-${stockItemId}`, content: <StockDetailContent stockItemId={stockItemId} embedded /> })
  }

  const [generateOpen, setGenerateOpen] = useState(false)


  // The configurator runs in an overlay pushed with a closure; reading the
  // offer through a ref keeps its If-Match current even when the advisor
  // autosaved another container while the overlay was open.
  const latestOffer = useRef<SalesOfferRead | undefined>(undefined)
  latestOffer.current = offerQuery.data

  const patchOffer = async (patch: Record<string, unknown>) => {
    const offer = latestOffer.current
    if (!offer) return
    const updated = await api.patch<SalesOfferRead>(`/sales/offers/${id}`, patch, { 'If-Match': String(offer.version) })
    queryClient.setQueryData(['sales-offer', id], updated)
  }

  // KAN-8 — same fix as PriceBuildUp.tsx's own fields, same root cause:
  // a NumberInput bound straight to a per-keystroke autosave PATCH races
  // itself and drops digits. Called unconditionally (rules of hooks),
  // safe during the loading state below since `offerQuery.data` is
  // simply undefined until it resolves — see the hook's own docstring.
  const [leasingDownPayment, setLeasingDownPayment] = useDebouncedNumberField(
    offerQuery.data?.leasingDownPayment != null ? Number(offerQuery.data.leasingDownPayment) : null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingDownPayment: value })
  )
  const [leasingTermMonths, setLeasingTermMonths] = useDebouncedNumberField(
    offerQuery.data?.leasingTermMonths ?? null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingTermMonths: value })
  )
  const [leasingKmPerYear, setLeasingKmPerYear] = useDebouncedNumberField(
    offerQuery.data?.leasingKmPerYear ?? null,
    offerQuery.data?.id,
    (value) => patchOffer({ leasingKmPerYear: value })
  )

  if (offerQuery.isLoading) return <Loader />
  if (offerQuery.isError || !offerQuery.data) {
    return (
      <Alert color="red" title={t('offerDetail.errors.failedToLoad')}>
        {offerQuery.error instanceof ApiError ? offerQuery.error.message : t('offerDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const offer = offerQuery.data
  const onOfferUpdated = (updated: SalesOfferRead) => queryClient.setQueryData(['sales-offer', id], updated)
  const containerProps = {
    ownerType: 'offer' as const,
    deal: offer,
    getDeal: () => latestOffer.current,
    patch: patchOffer,
    onDealUpdated: onOfferUpdated,
  }
  // KAN-64 — must match services/offer.py::confirm_offer's own gate
  // (status != "complete") exactly, or the button stays enabled for a
  // container the backend will still reject at the final confirm step.
  // Pricing's third status, "in_progress" (a vehicle is selected but no
  // usable price exists yet), used to slip through here because this
  // only checked for "not_started".
  const missing = (offer.containers ?? []).filter((c) => c.requirement === 'required' && c.status !== 'complete')

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2}>{t('offerWorkspace.title')}</Title>
        <Text c="dimmed">{offer.offerNumber} · {t(`salesEnums.dealStatus.${offer.status}`)}</Text>
      </Group>

      <DealCustomerContainer deal={offer} patch={patchOffer} onOpenCustomer={openCustomerOverlay} />
      <DealVehicleContainer {...containerProps} />
      <DealPricingContainer {...containerProps} onOpenStockItem={openStockItemOverlay} />
      <DealTradeInContainer {...containerProps} />

      {/* Leasing — S-D03: a refused calculator, free-text only */}
      <OverviewCard title={t('offerWorkspace.containers.leasing')} badge={<RequirementBadge requirement="optional" />}>
        <Stack gap="xs">
          <Text size="sm" c="dimmed">{t('offerWorkspace.leasing.hint')}</Text>
          <Group grow>
            <NumberInput
              label={t('offerWorkspace.leasing.downPayment')}
              value={leasingDownPayment}
              onChange={(v) => setLeasingDownPayment(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label={t('offerWorkspace.leasing.termMonths')}
              value={leasingTermMonths}
              onChange={(v) => setLeasingTermMonths(v === '' ? '' : Number(v))}
            />
            <NumberInput
              label={t('offerWorkspace.leasing.kmPerYear')}
              value={leasingKmPerYear}
              onChange={(v) => setLeasingKmPerYear(v === '' ? '' : Number(v))}
            />
          </Group>
        </Stack>
      </OverviewCard>

      <StickyActionFooter
        missingLabel={
          missing.length > 0
            ? `${t('offerWorkspace.missing')}: ${missing.map((m) => t(`offerWorkspace.containers.${m.id === 'trade_in' ? 'tradeIn' : m.id}`)).join(', ')}`
            : null
        }
        primaryAction={
          <Button
            disabled={missing.length > 0 || selectedCustomer?.lifecycleStatus === 'do_not_contact'}
            onClick={() => setGenerateOpen(true)}
          >
            {t('offerWorkspace.continue')}
          </Button>
        }
      />

      <OfferGenerateReviewModal
        opened={generateOpen}
        onClose={() => setGenerateOpen(false)}
        offer={offer}
        onFinalized={(finalized) => {
          queryClient.setQueryData(['sales-offer', id], finalized)
          setGenerateOpen(false)
          navigate(`/sales/offers/${id}`)
        }}
      />

    </Stack>
  )
}
