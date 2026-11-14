import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Alert, Button, Group, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, FormDialog, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { ActivationRead, CurrentLegalDocuments, LegalDocumentRead, SetupChecklistRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { LegalDocumentsToAccept } from '../components/setup/LegalDocumentsToAccept'
import { apiRefusalText } from '../utils/apiRefusal'

export const setupChecklistKey = (dealershipId: string) => ['setup-checklist', dealershipId]

/**
 * Dealer Administration › Setup (KAN-214; D-A-21, FR-A-26, FR-A-34,
 * FR-A-39). The manager's first screen until «Betrieb aktivieren»: every
 * item that applies to the Betrieb's entitlements, derived from its own data
 * (never ticked here), what it blocks and a link to the screen that
 * completes it; then activation with the ToS / DPA acceptance and the
 * number-range freeze warning. After activation the same list shows which
 * prerequisite is broken, since only its own action is blocked then.
 */
export function SetupChecklistPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('setup.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const [activating, setActivating] = useState(false)

  const checklist = useQuery({
    queryKey: [...setupChecklistKey(dealershipId), i18n.language],
    queryFn: () =>
      api.get<SetupChecklistRead>(`/dealerships/${dealershipId}/setup-checklist`, { 'Accept-Language': i18n.language }),
    enabled: dealershipId !== '',
  })
  const data = checklist.data
  const pending = data?.status === 'pending_onboarding'

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('setup.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('setup.subtitle')}
        </Text>
      </div>
      {checklist.isError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, checklist.error, 'setup.loadError')}
        </Alert>
      )}
      {checklist.isLoading && <Skeleton height={320} radius="md" />}
      {data && (
        <>
          <Alert color={pending ? 'yellow' : 'green'} variant="light">
            {pending ? t('setup.pendingNotice') : t('setup.activeNotice')}
          </Alert>
          <Paper withBorder radius="md">
            <Table striped aria-label={t('setup.itemsTitle')}>
              <Table.Tbody>
                {data.items.map((item) => (
                  <Table.Tr key={item.code}>
                    <Table.Td>
                      <Text fw={600}>{t(`setup.items.${item.code}.title`)}</Text>
                      <Text fz="sm" c="dimmed">
                        {t(`setup.items.${item.code}.description`)}
                      </Text>
                      <Text fz="xs" c="dimmed">
                        {pending ? t('setup.blocks.activation') : item.blocks ? t(`setup.blocks.${item.blocks}`) : null}
                      </Text>
                    </Table.Td>
                    <Table.Td w={120}>
                      {item.complete ? (
                        <Badge tone="success" dot>
                          {t('setup.complete')}
                        </Badge>
                      ) : (
                        <Badge tone="warning" dot>
                          {t('setup.missing')}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td w={110}>
                      <Button component={Link} to={item.link} size="xs" variant="subtle">
                        {t('setup.open')}
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
          {pending && (
            <Paper withBorder radius="md" p="md">
              <Stack gap="sm">
                <Title order={4}>{t('setup.legal.title')}</Title>
                <Text fz="sm">{t('setup.legal.intro')}</Text>
                {data.legalAcceptance.documents.length === 0 ? (
                  <Text fz="sm" c="dimmed">
                    {t('setup.legal.accepted')}
                  </Text>
                ) : (
                  <Group gap="xs">
                    <Text fz="sm">{t('setup.legal.due')}:</Text>
                    {data.legalAcceptance.documents.map((document) => (
                      <Badge key={document.id} tone="informational">
                        {`${t(`setup.documentTypes.${document.documentType}`)} · ${t('setup.legal.version', { version: document.version })}`}
                      </Badge>
                    ))}
                  </Group>
                )}
                {!data.legalDocumentsFinal && (
                  <Alert color="yellow" variant="light">
                    {t('setup.legal.notFinal')}
                  </Alert>
                )}
                <Group justify="flex-end">
                  {!data.complete && (
                    <Text fz="sm" c="dimmed">
                      {t('setup.activate.incomplete')}
                    </Text>
                  )}
                  <Button disabled={!data.complete || !data.legalDocumentsFinal} onClick={() => setActivating(true)}>
                    {t('setup.activate.button')}
                  </Button>
                </Group>
              </Stack>
            </Paper>
          )}
          <ActivationDialog
            opened={activating}
            onClose={() => setActivating(false)}
            dealershipId={dealershipId}
            checklist={data}
          />
        </>
      )}
    </Stack>
  )
}

function ActivationDialog({
  opened,
  onClose,
  dealershipId,
  checklist,
}: {
  opened: boolean
  onClose: () => void
  dealershipId: string
  checklist: SetupChecklistRead
}) {
  const { t, i18n } = useTranslation()
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [accepted, setAccepted] = useState<Record<string, boolean>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const dueTypes = new Set(checklist.legalAcceptance.documents.map((document) => document.documentType))
  const documents = useQuery({
    queryKey: ['legal-documents-current', i18n.language],
    queryFn: () => api.get<CurrentLegalDocuments>(`/legal-documents/current?language=${encodeURIComponent(i18n.language)}`),
    enabled: opened,
  })
  const toAccept: LegalDocumentRead[] = (documents.data?.items ?? []).filter((document) => dueTypes.has(document.documentType))
  const allTicked = toAccept.every((document) => accepted[document.id])

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      size="lg"
      title={t('setup.activate.title')}
      submitLabel={t('setup.activate.submit')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!documents.data || !allTicked}
      onSubmit={async () => {
        setSubmitting(true)
        setError(null)
        try {
          for (const document of toAccept) {
            await api.post(`/dealerships/${dealershipId}/legal-acceptances`, { legalDocumentId: document.id })
          }
          await api.post<ActivationRead>(`/dealerships/${dealershipId}/activation`, undefined, {
            'If-Match': String(checklist.version),
          })
          await queryClient.invalidateQueries({ queryKey: setupChecklistKey(dealershipId) })
          await refresh()
          onClose()
          navigate('/')
        } catch (err) {
          setError(apiRefusalText(t, err, 'setup.activate.error'))
          void queryClient.invalidateQueries({ queryKey: setupChecklistKey(dealershipId) })
        } finally {
          setSubmitting(false)
        }
      }}
    >
      <Stack gap="md">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        {documents.isError && (
          <Alert color="red" role="alert">
            {t('setup.activate.documentsError')}
          </Alert>
        )}
        {toAccept.length > 0 && <Text fz="sm">{t('setup.activate.intro')}</Text>}
        <LegalDocumentsToAccept
          documents={toAccept}
          accepted={accepted}
          onToggle={(id, value) => setAccepted((current) => ({ ...current, [id]: value }))}
        />
        <Alert color="yellow" variant="light">
          {t('setup.activate.freezeWarning')}
        </Alert>
      </Stack>
    </FormDialog>
  )
}
