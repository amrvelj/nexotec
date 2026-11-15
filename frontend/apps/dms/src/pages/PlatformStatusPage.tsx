import { useState } from 'react'
import { Alert, Button, Group, Paper, Select, Skeleton, Stack, Table, Text, TextInput, Textarea, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, FormDialog, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { IncidentSeverity, StatusIncidentCreate, StatusIncidentList, StatusIncidentRead } from '../api/types'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { languageEndonym } from '../i18n/languageEndonyms'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateTime } from '../utils/format'

const incidentsKey = ['platform-status-incidents']
const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
type Language = (typeof LANGUAGES)[number]
type Texts = Record<Language, string>
const SEVERITIES: IncidentSeverity[] = ['warning', 'blocked']

const emptyTexts = (): Texts => ({ de: '', fr: '', it: '', en: '' })

/** `datetime-local`'s value for an ISO instant, in the browser's zone. */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function fromLocalInput(value: string): string | null {
  return value ? new Date(value).toISOString() : null
}

function phase(row: StatusIncidentRead, now: Date): 'current' | 'ended' | 'scheduled' {
  if (new Date(row.startsAt) > now) return 'scheduled'
  if (row.endsAt && new Date(row.endsAt) <= now) return 'ended'
  return 'current'
}

/**
 * Platform Administration › Nexotec status (KAN-223; D-A-38: «Nexotec status
 * from a platform-maintained status record — no external status-page
 * dependency in v1»). Staff open an incident with its title and text in the
 * four UI languages; a current one shows on every Betrieb's status page until
 * it is ended. An incident is never deleted. Staff only at the API (a second
 * factor in the session to write); the entry is never role-hidden.
 */
export function PlatformStatusPage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const language = (LANGUAGES as readonly string[]).includes(i18n.language) ? (i18n.language as Language) : 'en'
  useSetBreadcrumb([t('shell.nav.administration'), t('statusPage.platform.title')])
  const [editing, setEditing] = useState<StatusIncidentRead | 'new' | null>(null)
  const queryClient = useQueryClient()
  const [endError, setEndError] = useState<string | null>(null)

  const incidents = useQuery({
    queryKey: incidentsKey,
    queryFn: () => api.get<StatusIncidentList>('/platform/status-incidents'),
  })
  const now = new Date()

  const end = async (row: StatusIncidentRead) => {
    setEndError(null)
    try {
      await api.patch<StatusIncidentRead>(
        `/platform/status-incidents/${row.id}`,
        { endsAt: new Date().toISOString() },
        { 'If-Match': String(row.version) }
      )
      await queryClient.invalidateQueries({ queryKey: incidentsKey })
    } catch (err) {
      setEndError(apiRefusalText(t, err, 'statusPage.platform.saveError'))
    }
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('statusPage.platform.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('statusPage.platform.subtitle')}
          </Text>
        </div>
        <Button onClick={() => setEditing('new')}>{t('statusPage.platform.new')}</Button>
      </Group>
      {(incidents.isError || endError) && (
        <Alert color="red" role="alert">
          {endError ?? apiRefusalText(t, incidents.error, 'statusPage.platform.loadError')}
        </Alert>
      )}
      {incidents.isLoading && <Skeleton height={240} radius="md" />}
      {incidents.data && incidents.data.items.length === 0 && <Text c="dimmed">{t('statusPage.platform.empty')}</Text>}
      {incidents.data && incidents.data.items.length > 0 && (
        <Paper withBorder radius="md">
          <Table striped aria-label={t('statusPage.platform.title')}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('statusPage.platform.columns.severity')}</Table.Th>
                <Table.Th>{t('statusPage.platform.columns.title')}</Table.Th>
                <Table.Th>{t('statusPage.platform.columns.from')}</Table.Th>
                <Table.Th>{t('statusPage.platform.columns.to')}</Table.Th>
                <Table.Th>{t('statusPage.platform.columns.state')}</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {incidents.data.items.map((row) => {
                const rowPhase = phase(row, now)
                return (
                  <Table.Tr key={row.id}>
                    <Table.Td>
                      <Badge tone={row.severity === 'blocked' ? 'destructive' : 'warning'} dot>
                        {t(`statusPage.platform.severity.${row.severity}`)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{row.title[language]}</Table.Td>
                    <Table.Td>{formatDateTime(row.startsAt, locale)}</Table.Td>
                    <Table.Td>{row.endsAt ? formatDateTime(row.endsAt, locale) : '–'}</Table.Td>
                    <Table.Td>{t(`statusPage.platform.${rowPhase}`)}</Table.Td>
                    <Table.Td>
                      <Group gap="xs" justify="flex-end" wrap="nowrap">
                        <Button size="xs" variant="subtle" onClick={() => setEditing(row)}>
                          {t('statusPage.platform.edit')}
                        </Button>
                        {rowPhase === 'current' && (
                          <Button size="xs" variant="light" color="red" onClick={() => void end(row)}>
                            {t('statusPage.platform.end')}
                          </Button>
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
      {editing !== null && (
        <IncidentDialog incident={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />
      )}
    </Stack>
  )
}

function IncidentDialog({ incident, onClose }: { incident: StatusIncidentRead | null; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [severity, setSeverity] = useState<IncidentSeverity>(incident?.severity ?? 'blocked')
  const [title, setTitle] = useState<Texts>(incident ? { ...incident.title } : emptyTexts())
  const [body, setBody] = useState<Texts>(incident ? { ...incident.body } : emptyTexts())
  const [startsAt, setStartsAt] = useState(toLocalInput(incident?.startsAt ?? new Date().toISOString()))
  const [endsAt, setEndsAt] = useState(toLocalInput(incident?.endsAt))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const complete = LANGUAGES.every((language) => title[language].trim() !== '' && body[language].trim() !== '')

  return (
    <FormDialog
      opened
      onClose={onClose}
      size="lg"
      title={incident ? t('statusPage.platform.form.editTitle') : t('statusPage.platform.form.createTitle')}
      submitLabel={t('statusPage.platform.form.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!complete || startsAt === ''}
      onSubmit={async () => {
        setSubmitting(true)
        setError(null)
        const payload: StatusIncidentCreate = {
          severity,
          title,
          body,
          startsAt: fromLocalInput(startsAt),
          endsAt: fromLocalInput(endsAt),
        }
        try {
          if (incident) {
            await api.patch<StatusIncidentRead>(`/platform/status-incidents/${incident.id}`, payload, {
              'If-Match': String(incident.version),
            })
          } else {
            await api.post<StatusIncidentRead>('/platform/status-incidents', payload)
          }
          await queryClient.invalidateQueries({ queryKey: incidentsKey })
          onClose()
        } catch (err) {
          setError(apiRefusalText(t, err, 'statusPage.platform.saveError'))
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
        <Text fz="sm" c="dimmed">
          {t('statusPage.platform.form.allLanguages')}
        </Text>
        <Select
          label={t('statusPage.platform.form.severity')}
          data={SEVERITIES.map((value) => ({ value, label: t(`statusPage.platform.severity.${value}`) }))}
          value={severity}
          onChange={(value) => value && setSeverity(value as IncidentSeverity)}
          allowDeselect={false}
        />
        {LANGUAGES.map((language) => (
          <Stack key={language} gap={4}>
            <TextInput
              label={t('statusPage.platform.form.titleIn', { language: languageEndonym(language) })}
              value={title[language]}
              maxLength={200}
              onChange={(event) => {
                const value = event.currentTarget.value
                setTitle((current) => ({ ...current, [language]: value }))
              }}
            />
            <Textarea
              label={t('statusPage.platform.form.bodyIn', { language: languageEndonym(language) })}
              value={body[language]}
              maxLength={2000}
              autosize
              minRows={2}
              onChange={(event) => {
                const value = event.currentTarget.value
                setBody((current) => ({ ...current, [language]: value }))
              }}
            />
          </Stack>
        ))}
        <Group grow>
          <TextInput
            type="datetime-local"
            label={t('statusPage.platform.form.startsAt')}
            value={startsAt}
            onChange={(event) => setStartsAt(event.currentTarget.value)}
          />
          <TextInput
            type="datetime-local"
            label={t('statusPage.platform.form.endsAt')}
            value={endsAt}
            onChange={(event) => setEndsAt(event.currentTarget.value)}
          />
        </Group>
      </Stack>
    </FormDialog>
  )
}
