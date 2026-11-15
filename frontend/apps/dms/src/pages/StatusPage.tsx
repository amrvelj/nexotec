import { Link } from 'react-router-dom'
import { Alert, Button, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge, useSetBreadcrumb, type BadgeTone } from '@nexotec/ui-kit'
import type { StatusItemRead, StatusState } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useStatusPage } from '../hooks/useStatusPage'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateTime } from '../utils/format'

const STATE_TONE: Record<StatusState, BadgeTone> = {
  ok: 'success',
  warning: 'warning',
  blocked: 'destructive',
}

const SOURCE_ORDER = ['number_ranges', 'integration', 'nexotec']

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
type Language = (typeof LANGUAGES)[number]

/** The item's sentence: its message key with its params, a number range's
 * document type in the UI language. */
function useItemText() {
  const { t } = useTranslation()
  return (item: StatusItemRead) => {
    const params: Record<string, string | number> = { ...item.params }
    if (typeof params.documentType === 'string') params.documentType = t(`numberRanges.documentType.${params.documentType}`)
    return t(item.messageKey, params)
  }
}

/**
 * Dealer Administration › Status («Alles grün»; KAN-223, D-A-38, FR-A-43).
 * One derived screen: every item each source computes from the Betrieb's own
 * data — number ranges, the integration connections, Nexotec's own status.
 * Green means every item is; anything else names the item and links to the
 * screen that fixes it. Nothing here is set by hand. Readable by every role
 * of the Betrieb (Anto, 2026-11-14, Q2 A); the shell's indicator is the
 * managers'.
 */
export function StatusPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('statusPage.title')])
  const { activeDealership } = useAuth()
  const status = useStatusPage(activeDealership?.id ?? '', true)
  const itemText = useItemText()
  // An incident carries all four languages (the API refuses one without);
  // a UI language outside them reads English, the app's fallback — never German.
  const language: Language = (LANGUAGES as readonly string[]).includes(i18n.language) ? (i18n.language as Language) : 'en'
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const data = status.data

  const sources = data
    ? [...new Set(data.items.map((item) => item.source))].sort(
        (a, b) => (SOURCE_ORDER.indexOf(a) + 1 || 99) - (SOURCE_ORDER.indexOf(b) + 1 || 99)
      )
    : []

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('statusPage.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('statusPage.subtitle')}
        </Text>
      </div>
      {status.isError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, status.error, 'statusPage.loadError')}
        </Alert>
      )}
      {status.isLoading && <Skeleton height={320} radius="md" />}
      {data && (
        <>
          <Paper withBorder radius="md" p="md" data-overall-state={data.state}>
            <Stack gap={4}>
              <Badge tone={STATE_TONE[data.state]} dot>
                {t(`statusPage.overall.${data.state}`)}
              </Badge>
              <Text fz="sm">
                {data.attentionCount === 0
                  ? t('statusPage.overallDetail.ok')
                  : t('statusPage.overallDetail.attention', { count: data.attentionCount })}
              </Text>
            </Stack>
          </Paper>
          {sources.map((source) => (
            <Paper key={source} withBorder radius="md">
              <Table striped aria-label={t(`statusPage.sources.${source}`)}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th colSpan={3}>{t(`statusPage.sources.${source}`)}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.items
                    .filter((item) => item.source === source)
                    .map((item) => (
                      <Table.Tr key={item.key}>
                        <Table.Td>
                          {item.incident ? (
                            <>
                              <Text fw={600}>{item.incident.title[language]}</Text>
                              <Text fz="sm">{item.incident.body[language]}</Text>
                              <Text fz="xs" c="dimmed">
                                {t('statusPage.since', { date: formatDateTime(item.incident.startsAt, locale) })}
                                {item.incident.endsAt
                                  ? ` · ${t('statusPage.until', { date: formatDateTime(item.incident.endsAt, locale) })}`
                                  : ''}
                              </Text>
                            </>
                          ) : (
                            <Text>{itemText(item)}</Text>
                          )}
                        </Table.Td>
                        <Table.Td w={140}>
                          <Badge tone={STATE_TONE[item.state]} dot>
                            {t(`statusPage.states.${item.state}`)}
                          </Badge>
                        </Table.Td>
                        <Table.Td w={110}>
                          {item.link && item.state !== 'ok' && (
                            <Button component={Link} to={item.link} size="xs" variant="subtle">
                              {t('statusPage.open')}
                            </Button>
                          )}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                </Table.Tbody>
              </Table>
            </Paper>
          ))}
        </>
      )}
    </Stack>
  )
}
