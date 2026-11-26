import { Paper, SimpleGrid, Stack, Text } from '@mantine/core'
import { Scale } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { slate, typography } from '@nexotec/ui-kit'
import type { ServiceOrderPayerTotalRead, ServiceOrderTotalsRead } from '../../../../api/types'
import { formatCurrencyChf } from '../../../../utils/format'
import { decimal, formatVatRate } from '../../serviceOrderOptions'

function Line({ label, amount, strong, testId }: { label: string; amount: string; strong?: boolean; testId?: string }) {
  return (
    <div
      data-testid={testId}
      style={{ display: 'flex', justifyContent: 'space-between', gap: 24, fontWeight: strong ? 700 : 400, fontSize: 14 }}
    >
      <span>{label}</span>
      <span style={{ fontFamily: typography.mono.family }}>{amount}</span>
    </div>
  )
}

function PayerBlock({ total, payerLabel, part }: { total: ServiceOrderPayerTotalRead; payerLabel: string; part: string }) {
  const { t } = useTranslation()
  return (
    <Stack gap={2} data-testid={`totals-${part}-${total.payer}`} style={{ maxWidth: 320 }}>
      <Text size="xs" fw={600} tt="uppercase" c={slate[5]}>
        {payerLabel}
      </Text>
      <Line label={t('serviceOrder.totals.net')} amount={formatCurrencyChf(decimal(total.net))} />
      {total.vatLines.map((line) => (
        <Line key={line.rate} label={t('serviceOrder.totals.vatLine', { rate: formatVatRate(line.rate) })} amount={formatCurrencyChf(decimal(line.vat))} />
      ))}
      {decimal(total.rounding) !== 0 && (
        <Line label={t('serviceOrder.totals.rounding')} amount={formatCurrencyChf(decimal(total.rounding))} testId={`totals-${part}-rounding`} />
      )}
      <div style={{ borderTop: `1px solid ${slate[3]}`, marginTop: 2, paddingTop: 2 }}>
        <Line label={t('serviceOrder.totals.total')} amount={formatCurrencyChf(decimal(total.roundedTotal))} strong />
      </div>
    </Stack>
  )
}

/**
 * «Auftragstotal je Zahler» (KAN-810 criterion 4): the server's totals
 * (`OrderTotalsRead`, the one money calculation, ADR-215) in two parts — the
 * jobs already accepted and those still open — per payer, with one VAT line
 * per rate and the cash rounding as its own line «Rundung». Nothing is added
 * up here. The payer of slice 1 is the billing category «Kunde», who is the
 * invoice recipient, named by `payerLabel`.
 */
export function OrderTotalsCard({ totals, payerLabel }: { totals: ServiceOrderTotalsRead; payerLabel: string }) {
  const { t } = useTranslation()
  const part = (key: 'accepted' | 'open', list: ServiceOrderPayerTotalRead[]) => (
    <Stack gap="xs" data-testid={`totals-${key}`}>
      <Text size="sm" fw={600}>
        {t(`serviceOrder.totals.${key}`)}
      </Text>
      {list.length === 0 ? (
        <Text size="sm" c="dimmed">
          {t('serviceOrder.totals.none')}
        </Text>
      ) : (
        list.map((total) => <PayerBlock key={total.payer} total={total} payerLabel={payerLabel} part={key} />)
      )}
    </Stack>
  )
  return (
    <Paper withBorder radius="md" p="lg">
      <Stack gap="md">
        <Text fw={600} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Scale size={18} />
          {t('serviceOrder.totals.title')}
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xl">
          {part('accepted', totals.accepted)}
          {part('open', totals.open)}
        </SimpleGrid>
        <Text size="xs" c="dimmed">
          {t('serviceOrder.totals.note')}
        </Text>
      </Stack>
    </Paper>
  )
}
