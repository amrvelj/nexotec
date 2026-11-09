import { Alert, Group, Stack, Table, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import type { ProviderQuoteRead } from '../api/types'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { formatCurrencyChf, formatDateOnly, formatDateTime, formatNumber } from '../utils/format'

type MessageLanguage = 'de' | 'fr' | 'it' | 'en'

function chf(value: string | null | undefined): string {
  return value != null ? formatCurrencyChf(Number(value)) : '—'
}

/**
 * KAN-552 — what the identAPI answered for one configured car: «Wert
 * auto-i-dat» is the trade-in (exchange) price, purchase and sale prices
 * beside it (Anto, 2026-11-09), the provider's own screen messages in the
 * user's language (provider text, as delivered — ADR-044), and the
 * predefined residual-value table as a read-only matrix (km per year × months).
 * Shown in the create dialog once fetched, and on the valuation detail.
 */
export function ProviderQuoteCard({ quote }: { quote: ProviderQuoteRead }) {
  const { t, i18n } = useTranslation()
  const language = (['de', 'fr', 'it', 'en'].includes(i18n.language) ? i18n.language : 'de') as MessageLanguage
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const messages = (quote.screenMessages ?? [])
    .map((m) => m[language] ?? m.de ?? m.fr ?? m.it ?? m.en)
    .filter((text): text is string => Boolean(text))

  const months = [...new Set(quote.residualTable.map((r) => r.durationMonths))].sort((a, b) => a - b)
  const kmBands = [...new Set(quote.residualTable.map((r) => r.kmPerYear))].sort((a, b) => a - b)
  const cell = (km: number, m: number) => quote.residualTable.find((r) => r.kmPerYear === km && r.durationMonths === m)

  const figures: [string, string, boolean?][] = [
    [t('valuationQuote.exchange'), chf(quote.exchangePrice), true],
    [t('valuationQuote.purchase'), chf(quote.purchasePrice)],
    [t('valuationQuote.sale'), chf(quote.salePrice)],
  ]
  if (quote.exchangeRangeFrom != null && quote.exchangeRangeTo != null) {
    figures.push([t('valuationQuote.range'), `${chf(quote.exchangeRangeFrom)} – ${chf(quote.exchangeRangeTo)}`])
  }
  if (quote.kilometreCorrectionExchange != null) {
    figures.push([t('valuationQuote.kmCorrection'), chf(quote.kilometreCorrectionExchange)])
  }
  if (quote.kilometreExpected != null) {
    figures.push([t('valuationQuote.kmExpected'), `${formatNumber(quote.kilometreExpected)} km`])
  }

  return (
    <Stack gap="xs" data-testid="provider-quote">
      {figures.map(([label, value, strong]) => (
        <Group key={label} justify="space-between">
          <Text size="sm" c={strong ? undefined : 'dimmed'} fw={strong ? 600 : undefined}>{label}</Text>
          <Text size="sm" fw={strong ? 600 : undefined}>{value}</Text>
        </Group>
      ))}
      <Text size="xs" c="dimmed">
        {t('valuationQuote.asked', {
          year: quote.modelYear,
          km: formatNumber(quote.mileage),
          date: formatDateOnly(quote.firstRegistration, locale),
          at: formatDateTime(quote.fetchedAt, locale),
        })}
      </Text>
      {quote.providerManual && (
        <Alert color="yellow" p="xs">
          <Text size="xs">{t('valuationQuote.manualHint')}</Text>
        </Alert>
      )}
      {messages.map((text) => (
        <Text key={text} size="xs" c="dimmed">{text}</Text>
      ))}

      <Text size="sm" fw={600} mt="xs">{t('valuationQuote.residualTitle')}</Text>
      {quote.residualTableState !== 'present' || quote.residualTable.length === 0 ? (
        <Text size="sm" c="dimmed">{t(`valuationQuote.residualState.${quote.residualTableState}`)}</Text>
      ) : (
        <Table withTableBorder striped fz="xs" aria-label={t('valuationQuote.residualTitle')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('valuationQuote.kmPerYear')}</Table.Th>
              {months.map((m) => (
                <Table.Th key={m} style={{ textAlign: 'right' }}>{t('valuationQuote.months', { months: m })}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {kmBands.map((km) => (
              <Table.Tr key={km}>
                <Table.Td>{formatNumber(km)}</Table.Td>
                {months.map((m) => {
                  const value = cell(km, m)
                  return (
                    <Table.Td key={m} style={{ textAlign: 'right' }} title={value?.totalPrice != null ? chf(value.totalPrice) : undefined}>
                      {value?.totalPercent != null ? `${formatNumber(Number(value.totalPercent), 1)} %` : '—'}
                    </Table.Td>
                  )
                })}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
