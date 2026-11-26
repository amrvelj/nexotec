import { useState, type ReactNode } from 'react'
import { Anchor, Button, Group, SimpleGrid, Stack, Text, Tooltip } from '@mantine/core'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { OverviewCard, slate } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type { ServiceOrderPartiesUpdate, ServiceOrderRead } from '../../../../api/types'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { formatDateOnly, formatDateTime } from '../../../../utils/format'
import { ContactChooseDialog } from '../ContactChooseDialog'
import { RefusalAlert } from './RefusalAlert'

type PartyRole = 'contact' | 'invoiceRecipient' | 'driver'

const FIELD: Record<PartyRole, keyof ServiceOrderPartiesUpdate> = {
  contact: 'contactId',
  invoiceRecipient: 'invoiceRecipientContactId',
  driver: 'driverContactId',
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Group justify="space-between" wrap="nowrap" gap="md" py={4} style={{ borderBottom: `1px solid ${slate[1]}` }}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Group gap="xs" wrap="nowrap">
        {children}
      </Group>
    </Group>
  )
}

/**
 * D3 «Übersicht» (KAN-810): the parties — ordering party, invoice recipient
 * and driver, each changeable by contact search (`PATCH`, If-Match; table 3.1:
 * refused once the order is frozen, a «Gesperrt» contact refused with its
 * reason) — the vehicle, and the order's location, date and creation.
 */
export function OverviewTab({
  order,
  canWrite,
  frozen,
  onOrder,
  onReload,
}: {
  order: ServiceOrderRead
  canWrite: boolean
  frozen: boolean
  onOrder: (order: ServiceOrderRead) => void
  onReload: () => void
}) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [choosing, setChoosing] = useState<PartyRole | null>(null)
  const [error, setError] = useState<unknown>(null)
  const editable = canWrite && !frozen
  // A missing role hides the actions; a frozen order shows them disabled with the reason.
  const frozenReason = frozen ? t('serviceOrder.actions.frozen') : null

  const roleLabel: Record<PartyRole, string> = {
    contact: t('serviceOrder.overview.contact'),
    invoiceRecipient: t('serviceOrder.overview.invoiceRecipient'),
    driver: t('serviceOrder.overview.driver'),
  }

  const change = async (changes: ServiceOrderPartiesUpdate) => {
    setError(null)
    try {
      onOrder(await api.patch<ServiceOrderRead>(`/service-orders/${order.id}`, changes, { 'If-Match': String(order.version) }))
    } catch (err) {
      setError(err)
    }
  }

  const changeButton = (role: PartyRole) =>
    canWrite ? (
      <Tooltip label={frozenReason} disabled={frozenReason === null}>
        <span>
          <Button
            size="compact-xs"
            variant="subtle"
            disabled={!editable}
            onClick={() => setChoosing(role)}
            aria-label={t('serviceOrder.overview.changeTitle', { role: roleLabel[role] })}
          >
            {t('serviceOrder.overview.change')}
          </Button>
        </span>
      </Tooltip>
    ) : null

  return (
    <Stack gap="md">
      {error !== null && (
        <RefusalAlert error={error} fallbackKey="serviceOrder.saveError" onReload={onReload} onDismiss={() => setError(null)} />
      )}
      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="md">
        <OverviewCard title={t('serviceOrder.overview.parties')}>
          <Row label={roleLabel.contact}>
            <Text size="sm" fw={500} data-testid="party-contact">
              {order.contact.label}
            </Text>
            {changeButton('contact')}
          </Row>
          <Row label={roleLabel.invoiceRecipient}>
            <Text size="sm" fw={500} data-testid="party-invoiceRecipient">
              {order.invoiceRecipient.label}
            </Text>
            {changeButton('invoiceRecipient')}
          </Row>
          <Row label={roleLabel.driver}>
            <Text size="sm" fw={500} data-testid="party-driver" fs={order.driver ? undefined : 'italic'} c={order.driver ? undefined : 'dimmed'}>
              {order.driver?.label ?? t('common.notSet')}
            </Text>
            {changeButton('driver')}
            {canWrite && order.driver && (
              <Button size="compact-xs" variant="subtle" color="gray" disabled={!editable} title={frozenReason ?? undefined} onClick={() => void change({ driverContactId: null })}>
                {t('serviceOrder.overview.removeDriver')}
              </Button>
            )}
          </Row>
        </OverviewCard>
        <OverviewCard title={t('serviceOrder.overview.vehicleCard')}>
          <Row label={t('serviceOrder.overview.vehicle')}>
            <Anchor component={Link} to={`/vehicles/${order.vehicleId}`} size="sm">
              {order.vehicleLabel}
            </Anchor>
          </Row>
        </OverviewCard>
        <OverviewCard title={t('serviceOrder.overview.orderCard')}>
          <Row label={t('serviceOrder.overview.orderNumber')}>
            <Text size="sm" ff="monospace">
              {order.orderNumber}
            </Text>
          </Row>
          <Row label={t('serviceOrder.overview.location')}>
            <Text size="sm">{order.locationLabel}</Text>
          </Row>
          <Row label={t('serviceOrder.overview.orderDate')}>
            <Text size="sm">{formatDateOnly(order.orderDate, locale)}</Text>
          </Row>
          <Row label={t('serviceOrder.overview.createdAt')}>
            <Text size="sm">{formatDateTime(order.createdAt, locale)}</Text>
          </Row>
        </OverviewCard>
      </SimpleGrid>

      <ContactChooseDialog
        opened={choosing !== null}
        title={choosing ? t('serviceOrder.overview.changeTitle', { role: roleLabel[choosing] }) : ''}
        onClose={() => setChoosing(null)}
        onChosen={(chosen) => {
          const role = choosing
          setChoosing(null)
          if (role) {
            const changes: ServiceOrderPartiesUpdate = {}
            changes[FIELD[role]] = chosen.id
            void change(changes)
          }
        }}
      />
    </Stack>
  )
}
