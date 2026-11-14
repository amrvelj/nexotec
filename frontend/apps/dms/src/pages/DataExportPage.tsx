import { useState } from 'react'
import { Alert, Button, Group, List, Stack, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileArchive } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { ExportPage, ExportRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { ExportsGrid } from '../components/dataExport/ExportsGrid'
import { downloadExport } from '../components/dataExport/downloadExport'
import { apiRefusalText } from '../utils/apiRefusal'

const dataExportsKey = (dealershipId: string) => ['data-exports', dealershipId] as const

/** While an export is being produced the list asks again, so it turns ready on its own. */
const POLL_MS = 5000

/**
 * KAN-218 — Dealer Administration › Datenexport (D-A-31, FR-A-44; Anto,
 * 2026-11-13). The manager requests a full export of the Betrieb at any time:
 * every record in open formats (CSV and JSON per entity, with a schema), every
 * issued document as PDF, the audit log and the uploaded files. The worker
 * produces it; the download stays seven days, then the file is deleted (Q1 A).
 * Acknowledging the final export is what offboarding needs (FR-A-45). The
 * matrix row «Data export» is the manager's; the entry is never role-hidden
 * (A-RULE-7) and a refused read says so.
 */
export function DataExportPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('dataExport.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('data_export')
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const exports = useQuery({
    queryKey: dataExportsKey(dealershipId),
    queryFn: () => api.get<ExportPage>(`/dealerships/${dealershipId}/exports?limit=100`),
    enabled: dealershipId !== '',
    retry: false,
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.status === 'requested') ? POLL_MS : false),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: dataExportsKey(dealershipId) })

  const request = useMutation({
    mutationFn: () => api.post<ExportRead>(`/dealerships/${dealershipId}/exports`),
    onSuccess: () => {
      setError(null)
      setNotice(t('dataExport.requested'))
      void refresh()
    },
    onError: (err) => setError(apiRefusalText(t, err, 'dataExport.requestError')),
  })
  const acknowledge = useMutation({
    mutationFn: (row: ExportRead) =>
      api.post<ExportRead>(`/dealerships/${dealershipId}/exports/${row.id}/acknowledge`, undefined, { 'If-Match': String(row.version) }),
    onSuccess: () => {
      setError(null)
      setNotice(t('dataExport.acknowledged'))
      void refresh()
    },
    onError: (err) => setError(apiRefusalText(t, err, 'dataExport.acknowledgeError')),
  })
  const download = async (row: ExportRead) => {
    setError(null)
    try {
      await downloadExport(`/dealerships/${dealershipId}/exports/${row.id}/download`, row.id)
    } catch (err) {
      setError(apiRefusalText(t, err, 'dataExport.downloadError'))
      void refresh()
    }
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('dataExport.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('dataExport.subtitle')}
          </Text>
        </div>
        {canWrite && (
          <Button leftSection={<FileArchive size={16} />} onClick={() => request.mutate()} loading={request.isPending}>
            {t('dataExport.request')}
          </Button>
        )}
      </Group>
      <Alert color="blue" variant="light" title={t('dataExport.contents.title')}>
        <List size="sm" spacing={2}>
          <List.Item>{t('dataExport.contents.records')}</List.Item>
          <List.Item>{t('dataExport.contents.documents')}</List.Item>
          <List.Item>{t('dataExport.contents.audit')}</List.Item>
          <List.Item>{t('dataExport.contents.files')}</List.Item>
        </List>
        <Text fz="sm" mt="xs">
          {t('dataExport.window')}
        </Text>
      </Alert>
      {!canWrite && (
        <Text c="dimmed" fz="sm">
          {t('dataExport.readOnly')}
        </Text>
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
      {exports.isError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, exports.error, 'dataExport.loadError')}
        </Alert>
      )}
      {!exports.isError && (
        <ExportsGrid
          gridKey="dataExports"
          ariaLabel={t('dataExport.listLabel')}
          exports={exports.data?.items ?? []}
          loading={exports.isLoading}
          onDownload={canWrite ? (row) => void download(row) : undefined}
          onAcknowledge={canWrite ? (row) => acknowledge.mutate(row) : undefined}
        />
      )}
      <Text c="dimmed" fz="xs">
        {t('dataExport.acknowledgeHint')}
      </Text>
    </Stack>
  )
}
