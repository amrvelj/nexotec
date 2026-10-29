import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Group, Loader, Stack, Text, UnstyledButton } from '@mantine/core'
import { FileText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DocumentPreview } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import { formatDateTime } from '../utils/format'
import type { SalesDocumentOwnerType, SalesDocumentPage, SalesDocumentRead } from '../api/types'

export interface SalesDocumentsSectionProps {
  ownerType: SalesDocumentOwnerType
  ownerId: string
  /** KAN-495 — an offer's live contract (`OfferRead.liveContractNumber`,
   * KAN-492): while it exists the offer prints no new document, so
   * «Dokument erzeugen» is disabled before the click and says why. */
  liveContractNumber?: string | null
}

/**
 * WP-8 PR-7 — the plain generate/list/preview shell: "Dokument erzeugen"
 * allocates the next version (append-only, never edited in place), the
 * version list is every prior generation, and selecting one re-renders it
 * deterministically from its own frozen `contentDefinition` (never a
 * cached PDF — there is no blob storage in this codebase). PR-8 extends
 * this same shell with the two-step build → review flow and the seller-
 * only margin panel beside the document (ADR-063, `DocumentPreview.
 * marginPanel`) — this component intentionally renders `marginPanel`-free
 * until then.
 */
export function SalesDocumentsSection({ ownerType, ownerId, liveContractNumber = null }: SalesDocumentsSectionProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  // KAN-266 — a generate retried after a lost response keeps its key, so the
  // server replays the version it built instead of building another. Every
  // generate that succeeds renews it: the next click is a new version.
  const generateKey = useIdempotencyKey()

  const blockedByContract = ownerType === 'offer' && liveContractNumber != null

  const basePath = ownerType === 'offer' ? `/sales/offers/${ownerId}` : `/sales/contracts/${ownerId}`

  const documentsQuery = useQuery({
    queryKey: ['sales-documents', ownerType, ownerId],
    queryFn: () => api.get<SalesDocumentPage>(`${basePath}/documents`),
  })

  const documents = documentsQuery.data?.items ?? []
  const newest = documents[0] ?? null

  useEffect(() => {
    if (selectedId == null && newest != null) setSelectedId(newest.id)
    // Only auto-select once a list is loaded and nothing is picked yet —
    // never overrides a selection the user already made.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newest?.id])

  useEffect(() => {
    if (!selectedId) {
      setPdfUrl(null)
      return
    }
    let cancelled = false
    let objectUrl: string | null = null
    setPdfError(null)
    void api
      .getBlobUrl(`/sales/documents/${selectedId}/pdf`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setPdfUrl(url)
      })
      .catch(() => {
        if (!cancelled) setPdfError(t('salesDocuments.previewError'))
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [selectedId, t])

  // KAN-67 — a contract with no vehicle or no price is refused by reason
  // (409), never printed empty; the refusal is said here in the user's
  // language, never the backend's English message. KAN-495 — an offer whose
  // contract was made after this page loaded is refused the same way.
  const generateRefusalMessage = (err: unknown): string => {
    const details = err instanceof ApiError && err.status === 409 ? (err.details as { reason?: unknown; contractNumber?: unknown } | null) : null
    const reason = details?.reason
    if (reason === 'missing_vehicle') return t('salesDocuments.errors.missingVehicle')
    if (reason === 'missing_price') return t('salesDocuments.errors.missingPrice')
    if (reason === 'offer_has_contract') return t('salesDocuments.liveContract', { contractNumber: String(details?.contractNumber ?? '—') })
    return t('salesDocuments.errors.generic')
  }

  const generate = async () => {
    setGenerating(true)
    setGenerateError(null)
    try {
      const path = `${basePath}/documents`
      const created = await api.post<SalesDocumentRead>(path, undefined, generateKey.headers([path]))
      generateKey.renew()
      await queryClient.invalidateQueries({ queryKey: ['sales-documents', ownerType, ownerId] })
      setSelectedId(created.id)
    } catch (err) {
      setGenerateError(generateRefusalMessage(err))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text fw={600} size="sm">
          {t('salesDocuments.title')}
        </Text>
        <Button
          size="xs"
          leftSection={<FileText size={14} />}
          loading={generating}
          disabled={blockedByContract}
          onClick={() => void generate()}
        >
          {t('salesDocuments.generate')}
        </Button>
      </Group>

      {/* An action, not a field: its reason is said (ADR-061; Anto, 2026-10-29). */}
      {blockedByContract && (
        <Text size="sm" c="dimmed">
          {t('salesDocuments.liveContract', { contractNumber: liveContractNumber })}
        </Text>
      )}

      {generateError && (
        <Alert color="red" py="xs" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setGenerateError(null)}>
          {generateError}
        </Alert>
      )}

      {documentsQuery.isLoading && <Loader size="sm" />}

      {!documentsQuery.isLoading && documents.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('salesDocuments.empty')}
        </Text>
      )}

      {documents.length > 0 && (
        <Group align="flex-start" gap="lg" wrap="nowrap">
          <Stack gap={4} style={{ flex: '0 0 200px' }}>
            {documents.map((doc) => (
              <UnstyledButton
                key={doc.id}
                onClick={() => setSelectedId(doc.id)}
                p="xs"
                style={{
                  borderRadius: 6,
                  backgroundColor: doc.id === selectedId ? 'var(--mantine-color-purple-1)' : undefined,
                }}
              >
                <Text size="sm" fw={doc.id === selectedId ? 600 : 400}>
                  {t('salesDocuments.version', { version: doc.version })}
                </Text>
                <Text size="xs" c="dimmed">
                  {formatDateTime(doc.renderedAt)} · {doc.correspondenceLanguage.toUpperCase()}
                </Text>
              </UnstyledButton>
            ))}
          </Stack>

          <div style={{ flex: 1, minWidth: 0 }}>
            <DocumentPreview
              src={pdfUrl}
              title={selectedId ? t('salesDocuments.version', { version: documents.find((d) => d.id === selectedId)?.version ?? '' }) : ''}
              correspondenceLanguage={documents.find((d) => d.id === selectedId)?.correspondenceLanguage?.toUpperCase()}
              loading={pdfUrl == null && pdfError == null}
              error={pdfError}
              onDownload={() => {
                if (pdfUrl) window.open(pdfUrl, '_blank', 'noopener')
              }}
              downloadLabel={t('salesDocuments.download')}
              loadingLabel={t('common.loading')}
            />
          </div>
        </Group>
      )}
    </Stack>
  )
}
