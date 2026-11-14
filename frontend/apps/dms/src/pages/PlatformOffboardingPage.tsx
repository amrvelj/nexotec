import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Checkbox, Group, Modal, Paper, Select, SimpleGrid, Stack, Text, Textarea, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, LogOut, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { DealershipPage, ExportPage, ExportRead, OffboardingRead } from '../api/types'
import { ExportsGrid } from '../components/dataExport/ExportsGrid'
import { downloadExport } from '../components/dataExport/downloadExport'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateOnly, formatDateTime } from '../utils/format'

/** Every Betrieb, page by page (the cursor list caps a page at 100). */
async function allDealerships(): Promise<DealershipPage['items']> {
  const items: DealershipPage['items'] = []
  let cursor: string | null = null
  do {
    const page: DealershipPage = await api.get<DealershipPage>(`/dealerships?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
    items.push(...page.items)
    cursor = page.nextCursor ?? null
  } while (cursor)
  return items
}

const offboardingKey = (dealershipId: string) => ['platform', 'offboarding', dealershipId] as const
const platformExportsKey = (dealershipId: string) => ['platform', 'exports', dealershipId] as const

/**
 * KAN-218 — Platform Administration › Offboarding (Dealer Administration
 * FR-A-45; Anto, 2026-11-13). Staff pick a Betrieb and see where it stands:
 * its last write, the export its manager acknowledged, and why offboarding is
 * refused while it is. Offboarding deactivates every user and starts the
 * ten-year archive (Q3 A); there is no dealer login after it (Q2 B), so a
 * former manager's copy is an archive export staff produce here, with the
 * reason the audit keeps. Staff-only at the API with a second factor; the
 * entry is never role-hidden (A-RULE-7) and a refusal says so.
 */
export function PlatformOffboardingPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('offboarding.title')])
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const dealershipId = searchParams.get('dealershipId') ?? ''
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [offboardReason, setOffboardReason] = useState('')
  const [archiveReason, setArchiveReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const dealerships = useQuery({ queryKey: ['platform', 'offboarding', 'dealerships'], queryFn: allDealerships, retry: false })
  const state = useQuery({
    queryKey: offboardingKey(dealershipId),
    queryFn: () => api.get<OffboardingRead>(`/platform/dealerships/${dealershipId}/offboarding`),
    enabled: dealershipId !== '',
    retry: false,
  })
  const exports = useQuery({
    queryKey: platformExportsKey(dealershipId),
    queryFn: () => api.get<ExportPage>(`/platform/dealerships/${dealershipId}/exports?limit=100`),
    enabled: dealershipId !== '',
    retry: false,
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.status === 'requested') ? 5000 : false),
  })
  const forbidden = [dealerships.error, state.error, exports.error].some((err) => err instanceof ApiError && err.status === 403)
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: offboardingKey(dealershipId) })
    void queryClient.invalidateQueries({ queryKey: platformExportsKey(dealershipId) })
  }

  const options = useMemo(
    () => (dealerships.data ?? []).map((d) => ({ value: d.id, label: d.tradeName || d.legalName })),
    [dealerships.data],
  )
  const choose = (value: string | null) => {
    setError(null)
    setNotice(null)
    const next = new URLSearchParams(searchParams)
    if (value) next.set('dealershipId', value)
    else next.delete('dealershipId')
    setSearchParams(next, { replace: true })
  }

  const offboard = useMutation({
    mutationFn: (current: OffboardingRead) =>
      api.post<OffboardingRead>(
        `/platform/dealerships/${dealershipId}/offboard`,
        { confirmation: true, reason: offboardReason.trim() || null },
        { 'If-Match': String(current.version) },
      ),
    onSuccess: () => {
      setConfirming(false)
      setConfirmed(false)
      setOffboardReason('')
      setError(null)
      setNotice(t('offboarding.done'))
      refresh()
    },
    onError: (err) => {
      setConfirming(false)
      setError(apiRefusalText(t, err, 'offboarding.offboardError'))
      refresh()
    },
  })
  const archive = useMutation({
    mutationFn: () => api.post<ExportRead>(`/platform/dealerships/${dealershipId}/exports`, { reason: archiveReason.trim() }),
    onSuccess: () => {
      setArchiveReason('')
      setError(null)
      setNotice(t('offboarding.archive.requested'))
      refresh()
    },
    onError: (err) => setError(apiRefusalText(t, err, 'offboarding.archive.error')),
  })
  const download = async (row: ExportRead) => {
    setError(null)
    try {
      await downloadExport(`/platform/dealerships/${dealershipId}/exports/${row.id}/download`, row.id)
    } catch (err) {
      setError(apiRefusalText(t, err, 'dataExport.downloadError'))
    }
  }

  const current = state.data
  const stamp = (value: string | null | undefined) => (value ? formatDateTime(value, locale) : '—')
  const offboarded = current?.status === 'offboarded'

  return (
    <Stack gap="md">
      <Title order={2}>{t('offboarding.title')}</Title>
      <Text size="sm" c="dimmed">
        {t('offboarding.intro')}
      </Text>
      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('offboarding.forbidden.title')}>
          {t('offboarding.forbidden.body')}
        </Alert>
      )}
      {notice && (
        <Alert color="green" role="status" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Select
        label={t('offboarding.betrieb')}
        placeholder={t('offboarding.betriebPlaceholder')}
        data={options}
        value={dealershipId || null}
        onChange={choose}
        searchable
        clearable
        nothingFoundMessage={t('offboarding.noBetrieb')}
        w={420}
      />

      {current && (
        <Paper withBorder radius="md" p="md">
          <Group justify="space-between" align="flex-start" mb="sm">
            <div>
              <Title order={4}>{current.legalName}</Title>
              <Badge tone={offboarded ? 'slate' : 'success'} dot>
                {t(`offboarding.status.${current.status}`)}
              </Badge>
            </div>
            {!offboarded && (
              <Button color="red" leftSection={<LogOut size={16} />} disabled={current.refusal !== null} onClick={() => setConfirming(true)}>
                {t('offboarding.offboard')}
              </Button>
            )}
          </Group>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
            <Text fz="sm">{t('offboarding.fields.lastWriteAt', { when: stamp(current.lastWriteAt) })}</Text>
            <Text fz="sm">{t('offboarding.fields.acknowledgedExport', { when: stamp(current.acknowledgedExportProducedAt) })}</Text>
            {offboarded && <Text fz="sm">{t('offboarding.fields.offboardedAt', { when: stamp(current.offboardedAt) })}</Text>}
            {current.retentionUntil && (
              <Text fz="sm">{t('offboarding.fields.retentionUntil', { day: formatDateOnly(current.retentionUntil, locale) })}</Text>
            )}
          </SimpleGrid>
          {!offboarded && current.refusal && (
            <Alert color="yellow" variant="light" mt="sm" title={t('offboarding.notYet')}>
              {t(`offboarding.refusal.${current.refusal}`)}
            </Alert>
          )}
        </Paper>
      )}

      {current && offboarded && (
        <Paper withBorder radius="md" p="md">
          <Title order={5} mb="xs">
            {t('offboarding.archive.title')}
          </Title>
          <Text fz="sm" c="dimmed" mb="sm">
            {t('offboarding.archive.intro')}
          </Text>
          <Group align="flex-end">
            <Textarea
              label={t('offboarding.archive.reason')}
              placeholder={t('offboarding.archive.reasonPlaceholder')}
              value={archiveReason}
              onChange={(e) => setArchiveReason(e.currentTarget.value)}
              autosize
              minRows={2}
              w={420}
            />
            <Button leftSection={<Archive size={16} />} disabled={archiveReason.trim().length < 3} loading={archive.isPending} onClick={() => archive.mutate()}>
              {t('offboarding.archive.request')}
            </Button>
          </Group>
        </Paper>
      )}

      {dealershipId && !forbidden && (
        <ExportsGrid
          gridKey="platformExports"
          ariaLabel={t('offboarding.exportsLabel')}
          exports={exports.data?.items ?? []}
          loading={exports.isLoading}
          showKind
          // Staff hand over an archive export; the manager's own stays the manager's.
          onDownload={(row) => void download(row)}
          downloadableKind="archive"
        />
      )}

      <Modal opened={confirming && current !== undefined} onClose={() => setConfirming(false)} title={t('offboarding.confirm.title')} centered>
        <Stack gap="sm">
          <Text fz="sm">{t('offboarding.confirm.body', { name: current?.legalName ?? '' })}</Text>
          <Textarea
            label={t('offboarding.confirm.reason')}
            value={offboardReason}
            onChange={(e) => setOffboardReason(e.currentTarget.value)}
            autosize
            minRows={2}
          />
          <Checkbox label={t('offboarding.confirm.check')} checked={confirmed} onChange={(e) => setConfirmed(e.currentTarget.checked)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button color="red" disabled={!confirmed} loading={offboard.isPending} onClick={() => current && offboard.mutate(current)}>
              {t('offboarding.confirm.submit')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
