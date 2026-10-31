import { Card, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { formatNumber, slate } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { MissingTranslationPage } from '../../api/types'
import { formatDateTime } from '../../utils/format'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'

/** KAN-209 — the `missing_translation` log (FR-A-30: "reported, never
 * rendered blank"): keys a browser rendered as the loud marker. */
export function MissingTranslationsCard() {
  const { t, i18n } = useTranslation()
  const missing = useQuery({
    queryKey: ['ui-translations', 'missing'],
    queryFn: () => api.get<MissingTranslationPage>('/platform/ui-translations/missing'),
  })
  const locale = toSwissLocale((i18n.language as SupportedLanguage) ?? 'de')
  const items = missing.data?.items ?? []

  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="sm">
        <Stack gap={2}>
          <Title order={4}>{t('translations.missing.title')}</Title>
          <Text size="sm" c={slate[5]}>
            {t('translations.missing.subtitle')}
          </Text>
        </Stack>
        {missing.data && items.length === 0 && (
          <Text size="sm" c={slate[5]}>
            {t('translations.missing.empty')}
          </Text>
        )}
        {items.length > 0 && (
          <Table verticalSpacing="xs" fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('translations.missing.columns.key')}</Table.Th>
                <Table.Th>{t('translations.missing.columns.language')}</Table.Th>
                <Table.Th>{t('translations.missing.columns.count')}</Table.Th>
                <Table.Th>{t('translations.missing.columns.last')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((item) => (
                <Table.Tr key={`${item.language}:${item.key}`}>
                  <Table.Td style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{item.key}</Table.Td>
                  <Table.Td>{t(`translations.languages.${item.language}`)}</Table.Td>
                  <Table.Td>{formatNumber(item.reportCount)}</Table.Td>
                  <Table.Td>{formatDateTime(item.lastReportedAt, locale)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  )
}
