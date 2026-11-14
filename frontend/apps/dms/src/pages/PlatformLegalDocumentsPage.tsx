import { useState } from 'react'
import { Alert, Button, Group, NumberInput, Paper, Select, Skeleton, Stack, Table, Text, TextInput, Textarea, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, FormDialog, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { LegalDocumentList, LegalDocumentPublish, LegalDocumentRead, LegalDocumentType } from '../api/types'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { LANGUAGE_OPTIONS, languageEndonym } from '../i18n/languageEndonyms'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateOnly, todayLocalIsoDate } from '../utils/format'

const legalDocumentsKey = ['platform-legal-documents']
const DOCUMENT_TYPES: LegalDocumentType[] = ['tos', 'dpa']

/**
 * Platform Administration › Legal documents (KAN-214; FR-A-39, D-A-34;
 * Anto, 2026-11-13, Q3 A). Nexotec's Terms of Service and Data Processing
 * Agreement, one row per version and language. A published version is never
 * edited — a correction is a new, higher version; below 1.0 a version is a
 * draft and production refuses activation on it. Staff only at the API (a
 * second factor in the session to publish); the entry is never role-hidden.
 */
export function PlatformLegalDocumentsPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('setup.platform.title')])
  const [publishing, setPublishing] = useState(false)

  const documents = useQuery({
    queryKey: legalDocumentsKey,
    queryFn: () => api.get<LegalDocumentList>('/platform/legal-documents'),
  })

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('setup.platform.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('setup.platform.subtitle')}
          </Text>
        </div>
        <Button onClick={() => setPublishing(true)}>{t('setup.platform.publish.button')}</Button>
      </Group>
      {documents.isError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, documents.error, 'setup.platform.loadError')}
        </Alert>
      )}
      {documents.isLoading && <Skeleton height={240} radius="md" />}
      {documents.data && documents.data.items.length === 0 && <Text c="dimmed">{t('setup.platform.empty')}</Text>}
      {documents.data && documents.data.items.length > 0 && (
        <Paper withBorder radius="md">
          <Table striped aria-label={t('setup.platform.title')}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('setup.platform.columns.documentType')}</Table.Th>
                <Table.Th>{t('setup.platform.columns.version')}</Table.Th>
                <Table.Th>{t('setup.platform.columns.language')}</Table.Th>
                <Table.Th>{t('setup.platform.columns.effectiveDate')}</Table.Th>
                <Table.Th>{t('setup.platform.columns.grace')}</Table.Th>
                <Table.Th>{t('setup.platform.columns.title')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {documents.data.items.map((row: LegalDocumentRead) => (
                <Table.Tr key={row.id}>
                  <Table.Td>{t(`setup.documentTypes.${row.documentType}`)}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      {row.version}
                      {row.draft && (
                        <Badge tone="warning" dot>
                          {t('setup.legal.draft')}
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>{languageEndonym(row.language)}</Table.Td>
                  <Table.Td>{formatDateOnly(row.effectiveDate, locale)}</Table.Td>
                  <Table.Td>{row.acceptanceGraceDays}</Table.Td>
                  <Table.Td>{row.title}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
      <PublishDialog opened={publishing} onClose={() => setPublishing(false)} />
    </Stack>
  )
}

function PublishDialog({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const blank = (): LegalDocumentPublish => ({
    documentType: 'tos',
    version: '',
    language: 'de',
    effectiveDate: todayLocalIsoDate(),
    title: '',
    bodyMarkdown: '',
    acceptanceGraceDays: 30,
  })
  const [form, setForm] = useState<LegalDocumentPublish>(blank)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof LegalDocumentPublish>(key: K, value: LegalDocumentPublish[K]) =>
    setForm((current) => ({ ...current, [key]: value }))
  const valid = form.version.trim() !== '' && form.title.trim() !== '' && form.bodyMarkdown.trim() !== '' && !!form.effectiveDate

  return (
    <FormDialog
      opened={opened}
      onClose={() => {
        setForm(blank())
        setError(null)
        onClose()
      }}
      size="lg"
      title={t('setup.platform.publish.title')}
      submitLabel={t('setup.platform.publish.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!valid}
      onSubmit={async () => {
        setSubmitting(true)
        setError(null)
        try {
          await api.post<LegalDocumentRead>('/platform/legal-documents', { ...form, version: form.version.trim() })
          await queryClient.invalidateQueries({ queryKey: legalDocumentsKey })
          setForm(blank())
          onClose()
        } catch (err) {
          setError(apiRefusalText(t, err, 'setup.platform.publish.error'))
        } finally {
          setSubmitting(false)
        }
      }}
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Group grow>
          <Select
            label={t('setup.platform.publish.documentType')}
            data={DOCUMENT_TYPES.map((value) => ({ value, label: t(`setup.documentTypes.${value}`) }))}
            value={form.documentType}
            onChange={(value) => value && set('documentType', value as LegalDocumentType)}
            allowDeselect={false}
          />
          <Select
            label={t('setup.platform.publish.language')}
            data={LANGUAGE_OPTIONS}
            value={form.language}
            onChange={(value) => value && set('language', value as LegalDocumentPublish['language'])}
            allowDeselect={false}
          />
        </Group>
        <Group grow>
          <TextInput
            label={t('setup.platform.publish.version')}
            description={t('setup.platform.publish.versionHint')}
            value={form.version}
            onChange={(event) => set('version', event.currentTarget.value)}
          />
          <TextInput
            label={t('setup.platform.publish.effectiveDate')}
            type="date"
            value={form.effectiveDate}
            onChange={(event) => set('effectiveDate', event.currentTarget.value)}
          />
          <NumberInput
            label={t('setup.platform.publish.grace')}
            value={form.acceptanceGraceDays}
            min={0}
            max={365}
            allowDecimal={false}
            onChange={(value) => set('acceptanceGraceDays', typeof value === 'number' ? value : 30)}
          />
        </Group>
        <TextInput
          label={t('setup.platform.publish.documentTitle')}
          value={form.title}
          onChange={(event) => set('title', event.currentTarget.value)}
        />
        <Textarea
          label={t('setup.platform.publish.body')}
          autosize
          minRows={8}
          maxRows={20}
          value={form.bodyMarkdown}
          onChange={(event) => set('bodyMarkdown', event.currentTarget.value)}
        />
      </Stack>
    </FormDialog>
  )
}
