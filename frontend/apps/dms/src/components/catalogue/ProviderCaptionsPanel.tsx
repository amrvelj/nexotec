import { useState } from 'react'
import { Alert, Button, Group, Stack, Table, Text } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { CaptionFetchList } from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatDateTime } from '../../utils/format'

const QUERY_KEY = ['integrations', 'captions', 'fetches']
const LANGUAGES = ['de', 'fr', 'it', 'en'] as const

/**
 * KAN-588 — the vehicle-data provider's code vocabulary (captions) on the
 * catalogue administration, for platform staff: per provider and language,
 * when it was last fetched and how many codes came back
 * (`GET /v1/integrations/captions/fetches`), and «Vokabular aktualisieren»
 * (`POST …/captions/refresh`), which fetches the four languages again through
 * the Betrieb the staff user acts in, under their own token. No scheduled
 * refresh exists (Anto, 2026-11-07): this and «Verbindung testen» are the two.
 */
export function ProviderCaptionsPanel() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const queryClient = useQueryClient()
  const refreshKey = useIdempotencyKey()
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<{ color: 'green' | 'red'; text: string } | null>(null)

  const fetches = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.get<CaptionFetchList>('/integrations/captions/fetches'),
  })

  const refresh = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      const answer = await api.post<CaptionFetchList>('/integrations/captions/refresh', undefined, refreshKey.headers())
      refreshKey.renew()
      queryClient.setQueryData(QUERY_KEY, answer)
      setMessage({ color: 'green', text: t('providerCaptions.refreshed') })
    } catch (err: unknown) {
      refreshKey.renew()
      const fetched = err instanceof ApiError ? err.details?.languagesFetched : undefined
      const text =
        Array.isArray(fetched) && fetched.length > 0
          ? t('providerCaptions.partial', { languages: fetched.map((l) => String(l).toUpperCase()).join(', ') })
          : apiRefusalText(t, err, 'providerCaptions.refreshFailed')
      setMessage({ color: 'red', text })
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY })
    } finally {
      setRefreshing(false)
    }
  }

  const items = fetches.data?.items ?? []
  const providers = [...new Set(items.map((i) => i.provider))].sort()
  const cell = (provider: string, language: string) => items.find((i) => i.provider === provider && i.language === language)

  return (
    <Stack gap="sm" data-testid="provider-captions">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Text fw={600}>{t('providerCaptions.title')}</Text>
          <Text size="sm" c="dimmed">{t('providerCaptions.intro')}</Text>
        </Stack>
        <Button leftSection={<RefreshCw size={16} />} loading={refreshing} onClick={() => void refresh()}>
          {t('providerCaptions.refresh')}
        </Button>
      </Group>
      {message && <Alert color={message.color}>{message.text}</Alert>}
      {fetches.isError ? (
        <Alert color="red">{t('providerCaptions.loadError')}</Alert>
      ) : providers.length === 0 && !fetches.isLoading ? (
        <Text size="sm" c="dimmed">{t('providerCaptions.empty')}</Text>
      ) : (
        <Table withTableBorder striped fz="sm" aria-label={t('providerCaptions.title')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('providerCaptions.provider')}</Table.Th>
              {LANGUAGES.map((language) => (
                <Table.Th key={language}>{language.toUpperCase()}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {providers.map((provider) => (
              <Table.Tr key={provider}>
                <Table.Td>{provider}</Table.Td>
                {LANGUAGES.map((language) => {
                  const fetch = cell(provider, language)
                  return (
                    <Table.Td key={language}>
                      {fetch
                        ? t('providerCaptions.fetched', { at: formatDateTime(fetch.fetchedAt, locale), count: fetch.captionCount })
                        : t('providerCaptions.notFetched')}
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
