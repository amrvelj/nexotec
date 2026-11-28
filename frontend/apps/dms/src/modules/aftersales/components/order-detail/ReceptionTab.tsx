import type { ReactNode } from 'react'
import { Button, Group, Paper, Stack, Text } from '@mantine/core'
import { Car } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { slate } from '@nexotec/ui-kit'
import type { ServiceOrderRead } from '../../../../api/types'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { formatDateTime, formatNumber } from '../../../../utils/format'
import { serviceOrderHref } from '../../serviceOrderOptions'

function Row({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <Group justify="space-between" py={8} style={{ borderBottom: `1px solid ${slate[1]}` }} wrap="nowrap" data-testid={testId}>
      <Text size="sm" c={slate[5]}>
        {label}
      </Text>
      <Text size="sm" ta="right">
        {children}
      </Text>
    </Group>
  )
}

/**
 * D3 «Annahme» (KAN-813 criterion 6): the arrival of the car — who received
 * it, when and how — the mileage (flagged when lower than the last, check
 * 22), key tag, parking place, the remark, and every order the stay holds
 * (check 23). Without a stay, the way to «Fahrzeug ist da».
 */
export function ReceptionTab({
  order,
  onArrive,
}: {
  order: ServiceOrderRead
  /** Omitted when «Fahrzeug ist da» is not open to this user or this order. */
  onArrive?: () => void
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const stay = order.stay

  if (!stay) {
    return (
      <Paper withBorder radius="md" p="md">
        <Stack gap="sm" align="flex-start">
          <Text size="sm" c="dimmed">
            {t('serviceOrder.reception.notHere')}
          </Text>
          {onArrive && (
            <Button variant="default" leftSection={<Car size={16} />} onClick={onArrive}>
              {t('serviceOrder.arrival.title')}
            </Button>
          )}
        </Stack>
      </Paper>
    )
  }

  const dash = '—'
  return (
    <Paper withBorder radius="md" data-testid="reception">
      <Group gap="xs" p="md" style={{ borderBottom: `1px solid ${slate[2]}` }}>
        <Car size={18} />
        <Text fw={600}>{t('serviceOrder.reception.title')}</Text>
      </Group>
      <Stack gap={0} px="md" pb="sm">
        <Row label={t('serviceOrder.reception.received')} testId="reception-received">
          {[
            formatDateTime(stay.arrivedAt, locale),
            stay.arrivedByLabel ?? t('serviceOrder.history.unknownUser'),
            t(`serviceOrder.reception.channel.${stay.arrivalChannel}`),
          ].join(' · ')}
        </Row>
        <Row label={t('serviceOrder.arrival.mileage')} testId="reception-mileage">
          {`${formatNumber(stay.arrivalMileage)} km`}
          {stay.mileageBelowPrevious && stay.previousMileage != null && (
            <Text component="span" size="sm" c="orange" ml="xs">
              {t('serviceOrder.reception.belowPrevious', { km: formatNumber(stay.previousMileage) })}
            </Text>
          )}
        </Row>
        <Row label={t('serviceOrder.arrival.keyTag')}>{stay.keyTag ?? dash}</Row>
        <Row label={t('serviceOrder.arrival.parkingPlace')}>{stay.parkingPlace ?? dash}</Row>
        <Row label={t('serviceOrder.arrival.remark')}>{stay.remark ?? dash}</Row>
        <Row label={t('serviceOrder.reception.orders')} testId="reception-orders">
          {stay.orders.map((held, index) => (
            <span key={held.id}>
              {index > 0 && ', '}
              {held.id === order.id ? held.orderNumber : <Link to={serviceOrderHref(held.id)}>{held.orderNumber}</Link>}
            </span>
          ))}
        </Row>
      </Stack>
    </Paper>
  )
}
