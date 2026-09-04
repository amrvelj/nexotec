import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Blocks, CircleAlert, Check, Languages, Plus, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog, InlineEditField, semantic, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { MappingGapsQueue } from '../components/MappingGapsQueue'
import type { ReferenceValuePage, ReferenceValueRead } from '../api/types'
import { REFERENCE_LIST_CODES, type ReferenceListCode } from '../referenceLists'
import {
  LANGUAGE_FIELDS,
  deriveReferenceView,
  referenceRowLanguageErrors,
  referenceRowMatchesQuery,
} from '../utils/referenceData'

const isConflict = (err: unknown) => err instanceof ApiError && err.status === 409

export function ReferenceDataPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('referenceData.breadcrumb'), t('referenceData.title')])
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  // ADR-056 — the shareable grid state (which list, the filter) lives in
  // the URL; density / layout are the reader's own ergonomics and stay out.
  const { list } = deriveReferenceView(searchParams)

  const [query, setQuery] = useState(() => deriveReferenceView(searchParams).query)
  const [debouncedQuery] = useDebouncedValue(query, 200)

  const updateUrl = (patch: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null) next.delete(key)
          else next.set(key, value)
        }
        return next
      },
      { replace: true },
    )
  }

  useEffect(() => {
    updateUrl({ q: debouncedQuery || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery])

  const queryKey = ['reference-data', list] as const
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey,
    queryFn: () => api.get<ReferenceValuePage>(`/reference-data/${list}?limit=200`),
  })

  const forbidden = error instanceof ApiError && error.status === 403

  const rows = useMemo(() => data?.items ?? [], [data])
  const visibleRows = useMemo(
    () => (debouncedQuery ? rows.filter((r) => referenceRowMatchesQuery(r, debouncedQuery)) : rows),
    [rows, debouncedQuery],
  )

  const patchLabel = useMutation({
    mutationFn: (args: { row: ReferenceValueRead; field: (typeof LANGUAGE_FIELDS)[number]; value: string }) =>
      api.patch<ReferenceValueRead>(
        `/reference-data/${list}/${args.row.valueCode}`,
        { [args.field]: args.value },
        { 'If-Match': String(args.row.version) },
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData<ReferenceValuePage>(queryKey, (prev) =>
        prev
          ? { ...prev, items: prev.items.map((it) => (it.valueCode === updated.valueCode ? updated : it)) }
          : prev,
      )
    },
  })

  const [createOpen, setCreateOpen] = useState(false)

  const listOptions = useMemo(() => REFERENCE_LIST_CODES.map((code) => ({ value: code, label: code })), [])

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={2}>{t('referenceData.title')}</Title>
          <Text c="dimmed" size="sm">
            {t('referenceData.subtitle')}
          </Text>
        </Stack>
        <Badge color="grape" variant="light" leftSection={<ShieldCheck size={14} />}>
          platform_admin
        </Badge>
      </Group>

      {forbidden && (
        <Alert color="yellow" icon={<ShieldCheck size={16} />} title={t('referenceData.forbidden.title')}>
          {t('referenceData.forbidden.body')}
        </Alert>
      )}

      {/* ── Card 1: the reference list ──────────────────────────────── */}
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap" align="flex-start">
            <Languages size={18} />
            <Stack gap={2} style={{ flex: 1 }}>
              <Text fw={600}>{t('referenceData.list.title', { code: list })}</Text>
              <Text c="dimmed" size="sm">
                {t('referenceData.list.help')}
              </Text>
            </Stack>
          </Group>

          <Group gap="sm" wrap="wrap">
            <Select
              aria-label={t('referenceData.list.pickLabel')}
              data={listOptions}
              value={list}
              onChange={(value) => value && updateUrl({ list: value, q: null })}
              searchable
              w={260}
            />
            <TextInput
              aria-label={t('referenceData.list.filterLabel')}
              placeholder={t('referenceData.list.filterPlaceholder')}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              w={260}
            />
            <Button variant="light" leftSection={<Plus size={16} />} onClick={() => setCreateOpen(true)} ml="auto">
              {t('referenceData.create.trigger')}
            </Button>
          </Group>

          {isError && !forbidden && (
            <Alert color="red" title={t('referenceData.loadError')}>
              <Button size="xs" variant="default" mt="xs" onClick={() => void refetch()}>
                {t('common.retry')}
              </Button>
            </Alert>
          )}

          <Table.ScrollContainer minWidth={720}>
            <Table verticalSpacing="xs" horizontalSpacing="md" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={140}>{t('referenceData.columns.code')}</Table.Th>
                  <Table.Th>Deutsch</Table.Th>
                  <Table.Th>Français</Table.Th>
                  <Table.Th>Italiano</Table.Th>
                  <Table.Th>English</Table.Th>
                  <Table.Th w={44} aria-label={t('referenceData.columns.status')} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {isLoading && (
                  <Table.Tr>
                    <Table.Td colSpan={6}>
                      <Text c="dimmed" size="sm">
                        {t('common.loading')}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}

                {!isLoading && visibleRows.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={6}>
                      <Text c="dimmed" size="sm">
                        {debouncedQuery ? t('referenceData.emptyFiltered') : t('referenceData.empty')}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}

                {visibleRows.map((row) => {
                  const missing = referenceRowLanguageErrors(row)
                  return (
                    <Table.Tr
                      key={row.valueCode}
                      style={missing.size > 0 ? { background: semantic.destructive.surface } : undefined}
                    >
                      <Table.Td style={{ fontFamily: 'monospace' }}>{row.valueCode}</Table.Td>
                      {LANGUAGE_FIELDS.map((field) => {
                        const empty = missing.has(field)
                        return (
                          <Table.Td key={field}>
                            <InlineEditField
                              value={row[field]}
                              isEmpty={empty}
                              emptyLabel={t('referenceData.required')}
                              isConflict={isConflict}
                              onReload={() => void refetch()}
                              onSave={async (raw) => {
                                await patchLabel.mutateAsync({ row, field, value: raw })
                              }}
                            />
                          </Table.Td>
                        )
                      })}
                      <Table.Td>
                        {missing.size > 0 ? (
                          <Tooltip label={t('referenceData.missingTip')} position="left" withArrow>
                            <CircleAlert
                              size={16}
                              color={semantic.destructive.text}
                              aria-label={t('referenceData.missingTip')}
                            />
                          </Tooltip>
                        ) : (
                          <Check size={16} color={semantic.success.text} aria-hidden="true" />
                        )}
                      </Table.Td>
                    </Table.Tr>
                  )
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      </Card>

      {/* ── Card 2: the mapping-gap queue, a work list ──────────────── */}
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap" align="flex-start">
            <Blocks size={18} />
            <Stack gap={2} style={{ flex: 1 }}>
              <Text fw={600}>{t('referenceData.gaps.title')}</Text>
              <Text c="dimmed" size="sm">
                {t('referenceData.gaps.help')}
              </Text>
            </Stack>
          </Group>
          <MappingGapsQueue paramPrefix="gaps" />
        </Stack>
      </Card>

      <CreateValueDialog
        opened={createOpen}
        listCode={list}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          void queryClient.invalidateQueries({ queryKey })
        }}
      />
    </Stack>
  )
}

/* Create is a DIALOG (UI spec — Forms). Submit is disabled ONLY while the
   request is in flight — never for incompleteness; the server's 422 comes
   back and is shown field by field. */
function CreateValueDialog({
  opened,
  listCode,
  onClose,
  onCreated,
}: {
  opened: boolean
  listCode: ReferenceListCode
  onClose: () => void
  onCreated: () => void
}) {
  const { t } = useTranslation()
  const empty = { valueCode: '', labelDe: '', labelFr: '', labelIt: '', labelEn: '' }
  const [form, setForm] = useState(empty)
  const [submitting, setSubmitting] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (opened) {
      setForm(empty)
      setFieldErrors({})
      setFormError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened])

  const submit = async () => {
    setSubmitting(true)
    setFieldErrors({})
    setFormError(null)
    try {
      await api.post(`/reference-data/${listCode}`, { ...form }, { 'Idempotency-Key': crypto.randomUUID() })
      onCreated()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && err.details) {
        const next: Record<string, string> = {}
        for (const [key, value] of Object.entries(err.details)) {
          if (typeof value === 'string') next[key] = value
        }
        setFieldErrors(next)
        if (Object.keys(next).length === 0) setFormError(err.message)
      } else if (err instanceof ApiError) {
        setFormError(err.message)
      } else {
        setFormError(t('referenceData.create.error'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  const set = (key: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.currentTarget.value }))

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('referenceData.create.title', { code: listCode })}
      submitLabel={t('referenceData.create.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => void submit()}
    >
      <Stack gap="sm">
        {formError && (
          <Alert color="red" variant="light">
            {formError}
          </Alert>
        )}
        <TextInput
          label={t('referenceData.columns.code')}
          placeholder="mild_hybrid"
          value={form.valueCode}
          onChange={set('valueCode')}
          error={fieldErrors.valueCode}
          data-autofocus
          required
        />
        <TextInput label="Deutsch" value={form.labelDe} onChange={set('labelDe')} error={fieldErrors.labelDe} required />
        <TextInput label="Français" value={form.labelFr} onChange={set('labelFr')} error={fieldErrors.labelFr} required />
        <TextInput label="Italiano" value={form.labelIt} onChange={set('labelIt')} error={fieldErrors.labelIt} required />
        <TextInput label="English" value={form.labelEn} onChange={set('labelEn')} error={fieldErrors.labelEn} required />
        <Text c="dimmed" size="xs">
          {t('referenceData.create.allLanguagesHint')}
        </Text>
      </Stack>
    </FormDialog>
  )
}
