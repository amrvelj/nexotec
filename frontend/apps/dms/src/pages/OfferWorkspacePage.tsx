import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Loader, NumberInput, Stack, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { ChfInput, OverviewCard, StickyActionFooter, useOverlay, useSetBreadcrumb } from '@nexotec/ui-kit'
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
import { useDealSaveQueue } from '../hooks/useDealSaveQueue'
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
  // KAN-327 — a price typed inside useDebouncedNumberField's 500 ms window is
  // not yet a save (the field does not flush on blur), so `saves.pending`
  // alone would let Continue open Generate on the offer without it — and
  // the reviewed document must be what was entered (ADR-063). The contract
  // page waits for the same state (pricingUnsaved, KAN-288).
  const [pricingUnsaved, setPricingUnsaved] = useState(false)

  // KAN-314 — every container autosaves on its own, and since KAN-311 the
  // server takes the offer's row lock and checks If-Match on each save, so
  // two saves sent with one version cannot both land. The queue sends them
  // one after the other, each with the version the previous one returned.
  // It reads the offer from the query cache when a save is sent — never a
  // render's copy — so the configurator overlay, which calls `patchOffer`
  // from a closure, sends the current version too.
  const [saveError, setSaveError] = useState<string | null>(null)
  const saves = useDealSaveQueue<SalesOfferRead>(
    () => queryClient.getQueryData<SalesOfferRead>(['sales-offer', id]),
    (updated) => {
      setSaveError(null)
      queryClient.setQueryData(['sales-offer', id], updated)
    }
  )

  // A refused save is said in the user's language, keyed off the 409's
  // shape (never the backend's English message), and the offer is reloaded
  // so the screen shows what it now is.
  const saveRefusalMessage = (err: unknown): string => {
    if (err instanceof ApiError && err.status === 409 && (err.details ?? {}).currentVersion != null) {
      return t('offerWorkspace.errors.saveRefused.staleVersion')
    }
    return t('offerWorkspace.errors.saveRefused.generic')
  }

  const patchOffer = async (patch: Record<string, unknown>) => {
    try {
      await saves.save((current) =>
        api.patch<SalesOfferRead>(`/sales/offers/${id}`, patch, { 'If-Match': String(current.version) })
      )
    } catch (err) {
      setSaveError(saveRefusalMessage(err))
      void offerQuery.refetch()
      throw err
    }
  }
  // The leasing fields commit from an effect; the refusal is already on
  // screen (above), so it only must not go unhandled.
  const commitLeasing = (patch: Record<string, unknown>) => {
    patchOffer(patch).catch(() => undefined)
  }

  // KAN-8 — same fix as PriceBuildUp.tsx's own fields, same root cause:
  // a NumberInput bound straight to a per-keystroke autosave PATCH races
  // itself and drops digits. Called unconditionally (rules of hooks),
  // safe during the loading state below since `offerQuery.data` is
  // simply undefined until it resolves — see the hook's own docstring.
  const [leasingDownPayment, setLeasingDownPayment] = useDebouncedNumberField(
    offerQuery.data?.leasingDownPayment != null ? Number(offerQuery.data.leasingDownPayment) : null,
    offerQuery.data?.id,
    (value) => commitLeasing({ leasingDownPayment: value })
  )
  const [leasingTermMonths, setLeasingTermMonths] = useDebouncedNumberField(
    offerQuery.data?.leasingTermMonths ?? null,
    offerQuery.data?.id,
    (value) => commitLeasing({ leasingTermMonths: value })
  )
  const [leasingKmPerYear, setLeasingKmPerYear] = useDebouncedNumberField(
    offerQuery.data?.leasingKmPerYear ?? null,
    offerQuery.data?.id,
    (value) => commitLeasing({ leasingKmPerYear: value })
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
  const containerProps = {
    ownerType: 'offer' as const,
    deal: offer,
    patch: patchOffer,
    save: saves.save,
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

      {saveError && (
        <Alert
          color="red"
          role="alert"
          withCloseButton
          closeButtonLabel={t('offerWorkspace.errors.saveRefused.dismiss')}
          onClose={() => setSaveError(null)}
        >
          {saveError}
        </Alert>
      )}

      <DealCustomerContainer ownerType="offer" deal={offer} patch={patchOffer} onOpenCustomer={openCustomerOverlay} />
      <DealVehicleContainer {...containerProps} />
      <DealPricingContainer {...containerProps} onOpenStockItem={openStockItemOverlay} onUnsavedChange={setPricingUnsaved} />
      <DealTradeInContainer {...containerProps} />

      {/* Leasing — S-D03: a refused calculator, free-text only */}
      <OverviewCard title={t('offerWorkspace.containers.leasing')} badge={<RequirementBadge requirement="optional" />}>
        <Stack gap="xs">
          <Text size="sm" c="dimmed">{t('offerWorkspace.leasing.hint')}</Text>
          <Group grow>
            <ChfInput
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
            disabled={
              missing.length > 0 ||
              selectedCustomer?.lifecycleStatus === 'do_not_contact' ||
              pricingUnsaved ||
              saves.pending > 0
            }
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
