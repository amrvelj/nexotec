import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Group, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { CurrentLegalDocuments } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { LegalDocumentsToAccept } from '../components/setup/LegalDocumentsToAccept'
import { toSwissLocale, type SupportedLanguage } from '../i18n'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateOnly } from '../utils/format'

/**
 * KAN-214 (FR-A-39; Anto, 2026-11-13, Q1 A) — a new version of the ToS or
 * the DPA for an active Betrieb. The manager is prompted at every login until
 * the last day of grace; after it this is the only screen a manager reaches
 * (the server holds every other route with 403), while colleagues keep
 * working. Each document is accepted in full, in the UI language.
 */
export function LegalAcceptancePage() {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  useSetBreadcrumb([t('shell.nav.administration'), t('setup.acceptance.title')])
  const { activeDealership, legalAcceptanceDue, refresh } = useAuth()
  const navigate = useNavigate()
  const dealershipId = activeDealership?.id ?? ''
  const [accepted, setAccepted] = useState<Record<string, boolean>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const dueTypes = new Set((legalAcceptanceDue?.documents ?? []).map((document) => document.documentType))
  const documents = useQuery({
    queryKey: ['legal-documents-current', i18n.language],
    queryFn: () => api.get<CurrentLegalDocuments>(`/legal-documents/current?language=${encodeURIComponent(i18n.language)}`),
    enabled: dueTypes.size > 0,
  })
  const toAccept = (documents.data?.items ?? []).filter((document) => dueTypes.has(document.documentType))

  const accept = async () => {
    setSubmitting(true)
    setError(null)
    try {
      for (const document of toAccept) {
        await api.post(`/dealerships/${dealershipId}/legal-acceptances`, { legalDocumentId: document.id })
      }
      await refresh()
      navigate('/')
    } catch (err) {
      setError(apiRefusalText(t, err, 'setup.acceptance.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack gap="md" maw={880}>
      <div>
        <Title order={2}>{t('setup.acceptance.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('setup.acceptance.intro')}
        </Text>
      </div>
      {!legalAcceptanceDue && <Alert color="green">{t('setup.acceptance.done')}</Alert>}
      {legalAcceptanceDue?.held && (
        <Alert color="red" role="alert">
          {t('setup.acceptance.held')}
        </Alert>
      )}
      {legalAcceptanceDue && !legalAcceptanceDue.held && legalAcceptanceDue.dueBy && (
        <Alert color="yellow" variant="light">
          {t('setup.acceptance.dueBy', { date: formatDateOnly(legalAcceptanceDue.dueBy, locale) })}
        </Alert>
      )}
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
      {documents.isLoading && <Skeleton height={280} radius="md" />}
      {toAccept.length > 0 && (
        <>
          <LegalDocumentsToAccept
            documents={toAccept}
            accepted={accepted}
            onToggle={(id, value) => setAccepted((current) => ({ ...current, [id]: value }))}
          />
          <Group justify="flex-end">
            <Button loading={submitting} disabled={!toAccept.every((d) => accepted[d.id])} onClick={() => void accept()}>
              {t('setup.acceptance.submit')}
            </Button>
          </Group>
        </>
      )}
    </Stack>
  )
}
