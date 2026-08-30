import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Group, Loader, Stack, Text, UnstyledButton } from '@mantine/core'
import { FileText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DocumentPreview } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { formatDateTime } from '../utils/format'
import type { SalesDocumentOwnerType, SalesDocumentPage, SalesDocumentRead } from '../api/types'

export interface SalesDocumentsSectionProps {
  ownerType: SalesDocumentOwnerType
  ownerId: string
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
export function SalesDocumentsSection({ ownerType, ownerId }: SalesDocumentsSectionProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

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

  const generate = async () => {
    setGenerating(true)
    try {
      const created = await api.post<SalesDocumentRead>(`${basePath}/documents`)
      await queryClient.invalidateQueries({ queryKey: ['sales-documents', ownerType, ownerId] })
      setSelectedId(created.id)
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
        <Button size="xs" leftSection={<FileText size={14} />} loading={generating} onClick={() => void generate()}>
          {t('salesDocuments.generate')}
        </Button>
      </Group>

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
            />
          </div>
        </Group>
      )}
    </Stack>
  )
}
