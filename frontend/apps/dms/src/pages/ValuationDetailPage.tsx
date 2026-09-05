import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Group, Loader, Stack, Text } from '@mantine/core'
import { CarFront } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, OverviewCard, SpecGrid, ValuationSourceBadge, ValuationStatusBadge, useOverlay, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { buildValuationRowMenu } from '../components/valuationRowMenu'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatCurrencyChf, formatDate } from '../utils/format'
import { CustomerDetailContent } from './CustomerDetailPage'
import type { ValuationRead } from '../api/types'

export function ValuationDetailPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return null
  return <ValuationDetailContent valuationId={id} />
}

export interface ValuationDetailContentProps {
  valuationId: string
  /** § ADR-059 — true when rendered as an Overlay rather than the real
   * `/valuations/:id` route. */
  embedded?: boolean
}

/**
 * The standalone application's own detail screen (WP-8 PR-9) — verbatim
 * expired-banner copy from the confirmed reference prototype, the three-
 * tier Rohwert -> Abzüge -> Eintauschangebot card, and the on-read
 * validity help text ("wird beim Lesen berechnet, nicht über Nacht
 * nachgeführt"). No "Bearbeiten" anywhere — see valuationRowMenu.tsx's
 * own docstring on why a Valuation has none.
 */
export function ValuationDetailContent({ valuationId: id, embedded = false }: ValuationDetailContentProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const overlay = useOverlay()

  const valuationQuery = useQuery({
    queryKey: ['valuation', id],
    queryFn: () => api.get<ValuationRead>(`/valuations/${id}`),
    enabled: Boolean(id),
  })

  useSetBreadcrumb(embedded ? null : [t('shell.nav.valuations'), valuationQuery.data?.valuationNumber ?? id])

  const markUsed = async () => {
    const valuation = valuationQuery.data
    if (!valuation) return
    const updated = await api.post<ValuationRead>(`/valuations/${id}/mark-used`, undefined, {
      'If-Match': String(valuation.version),
    })
    queryClient.setQueryData(['valuation', id], updated)
  }

  // § ADR-059 — the customer behind this valuation opens as an overlay,
  // never a navigation away from the valuation itself. The vehicle side
  // is a plain navigation instead: VehicleDetailPage has no embeddable
  // *Content component yet (unlike Customer/Offer/Contract) — a WP-5-
  // side gap this PR doesn't fix, flagged rather than worked around.
  const openCustomerOverlay = (customerId: string) => {
    overlay.push({ key: `customer-overlay-${customerId}`, content: <CustomerDetailContent customerId={customerId} embedded /> })
  }

  if (valuationQuery.isLoading) return <Loader />
  if (valuationQuery.isError || !valuationQuery.data) {
    return (
      <Alert color="red" title={t('valuationDetail.errors.failedToLoad')}>
        {valuationQuery.error instanceof ApiError ? valuationQuery.error.message : t('valuationDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const valuation = valuationQuery.data
  const vehicleTitle = [valuation.vehicleMake, valuation.vehicleModel, valuation.vehicleTrim].filter(Boolean).join(' ') || valuation.vehicleVin || t('valuationDetail.untitled')

  const menu = buildValuationRowMenu(t, valuation, {
    onRevalue: () => navigate(`/valuations/new?supersedes=${id}`),
    onMarkUsed: () => void markUsed(),
    onOpenCustomer: valuation.customerId ? () => openCustomerOverlay(valuation.customerId!) : undefined,
    onOpenVehicle: valuation.vehicleId ? () => navigate(`/vehicles/${valuation.vehicleId}`) : undefined,
  })

  const deductionsTotal = (valuation.deductions ?? []).reduce((sum, d) => sum + Number(d.amount), 0)

  return (
    <Stack gap="lg">
      <DetailHeader
        entityMark={<CarFront size={24} />}
        title={vehicleTitle}
        businessKey={valuation.valuationNumber}
        badges={
          <>
            <ValuationStatusBadge status={valuation.status} />
            <ValuationSourceBadge source={valuation.source} />
          </>
        }
        primaryAction={menu.primary}
        overflowActions={menu.overflow}
      />

      {valuation.status === 'expired' && (
        // Confirmed live, verbatim.
        <Alert color="red" title={t('valuationDetail.expiredBanner.title')}>
          {t('valuationDetail.expiredBanner.body')}
        </Alert>
      )}

      <OverviewCard title={t('valuationDetail.valueCard.title')}>
        <Stack gap={4}>
          <Group justify="space-between">
            <Text size="sm" c="dimmed">{t('valuationDetail.valueCard.providerValue')}</Text>
            <Text size="sm">{valuation.providerValue != null ? formatCurrencyChf(Number(valuation.providerValue)) : '—'}</Text>
          </Group>
          {(valuation.deductions ?? []).map((d, i) => (
            <Group key={i} justify="space-between">
              <Text size="sm" c="dimmed">{d.label}</Text>
              <Text size="sm" c="red">− {formatCurrencyChf(Number(d.amount))}</Text>
            </Group>
          ))}
          {(valuation.deductions ?? []).length > 0 && (
            <Group justify="space-between">
              <Text size="sm" fw={600}>{t('valuationDetail.valueCard.netValue')}</Text>
              <Text size="sm" fw={600}>
                {valuation.providerValue != null ? formatCurrencyChf(Number(valuation.providerValue) - deductionsTotal) : '—'}
              </Text>
            </Group>
          )}
          <Group justify="space-between" mt="xs" pt="xs" style={{ borderTop: '1px solid var(--mantine-color-gray-3)' }}>
            <Text fw={700}>{t('valuationDetail.valueCard.finalOffer')}</Text>
            <Text fw={700}>{formatCurrencyChf(Number(valuation.finalOffer))}</Text>
          </Group>
          <Text size="xs" c="dimmed">{t('valuationDetail.valueCard.finalOfferHint')}</Text>
        </Stack>
      </OverviewCard>

      <OverviewCard title={t('valuationDetail.vehicleCard.title')}>
        <SpecGrid
          columns={2}
          items={[
            { label: t('valuationDetail.vehicleCard.vin'), value: valuation.vehicleVin ?? '—' },
            { label: t('valuationDetail.vehicleCard.plate'), value: valuation.vehiclePlate ?? '—' },
            { label: t('valuationDetail.vehicleCard.mileage'), value: valuation.mileage != null ? `${valuation.mileage.toLocaleString(i18n.language)} km` : '—' },
            { label: t('valuationDetail.vehicleCard.firstRegistration'), value: valuation.vehicleFirstRegistration ? formatDate(valuation.vehicleFirstRegistration, locale) : '—' },
            { label: t('valuationDetail.vehicleCard.customer'), value: valuation.customerLabel ?? t('valuationsList.noCustomer') },
          ]}
        />
      </OverviewCard>

      {valuation.note && (
        <OverviewCard title={t('valuationDetail.noteCard.title')}>
          <Text size="sm">{valuation.note}</Text>
        </OverviewCard>
      )}

      <Text size="xs" c="dimmed">
        {/* Confirmed live, on-read validity help copy — ADR-066/FR-V-17. */}
        {t('valuationDetail.validityHelp', { validUntil: formatDate(valuation.validUntil, locale) })}
      </Text>
    </Stack>
  )
}
