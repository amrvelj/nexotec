import { useEffect, useState } from 'react'
import { Alert, Button, Group, Loader, Modal, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { DocumentPreview } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { formatCurrencyChf } from '../utils/format'
import type { SalesDocumentRead, SalesOfferRead } from '../api/types'

export interface OfferGenerateReviewModalProps {
  opened: boolean
  onClose: () => void
  offer: SalesOfferRead
  /** Called once the seller confirms — the caller re-fetches the offer
   * (now `status: 'open'`) and closes this modal from its own success
   * path, since a fresh If-Match is needed for the next mutation anyway. */
  onFinalized: (offer: SalesOfferRead) => void
}

type Step = 'build' | 'review'

/**
 * WP-8 PR-8 (ADR-063) — "generating an offer is two steps: build, then
 * review the rendered document in the customer's correspondence language
 * with the seller-only margin panel BESIDE it, never on it." Step 1 calls
 * the plain document-generation endpoint built in PR-7
 * (POST .../documents — no status change); step 2 shows the result via
 * the existing DocumentPreview component's own `marginPanel` prop and
 * only THEN offers the explicit "Bestätigen" action that transitions the
 * offer out of draft (POST .../finalize).
 */
export function OfferGenerateReviewModal({ opened, onClose, offer, onFinalized }: OfferGenerateReviewModalProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState<Step>('build')
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [document, setDocument] = useState<SalesDocumentRead | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [finalizing, setFinalizing] = useState(false)
  const [finalizeError, setFinalizeError] = useState<string | null>(null)

  useEffect(() => {
    if (!opened) {
      setStep('build')
      setDocument(null)
      setPdfUrl(null)
      setBuildError(null)
      setFinalizeError(null)
    }
  }, [opened])

  useEffect(() => {
    if (!document) return
    let cancelled = false
    let objectUrl: string | null = null
    void api
      .getBlobUrl(`/sales/documents/${document.id}/pdf`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setPdfUrl(url)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [document])

  const build = async () => {
    setBuilding(true)
    setBuildError(null)
    try {
      const created = await api.post<SalesDocumentRead>(`/sales/offers/${offer.id}/documents`)
      setDocument(created)
      setStep('review')
    } catch (err) {
      setBuildError(err instanceof ApiError ? err.message : t('offerWorkspace.generate.buildError'))
    } finally {
      setBuilding(false)
    }
  }

  const confirm = async () => {
    setFinalizing(true)
    setFinalizeError(null)
    try {
      const finalized = await api.post<SalesOfferRead>(`/sales/offers/${offer.id}/finalize`, undefined, {
        'If-Match': String(offer.version),
      })
      onFinalized(finalized)
    } catch (err) {
      setFinalizeError(err instanceof ApiError ? err.message : t('offerWorkspace.generate.finalizeError'))
    } finally {
      setFinalizing(false)
    }
  }

  const marginPanel = (
    <Stack gap={4} p="xs" style={{ border: '1px dashed var(--mantine-color-red-4)', borderRadius: 6 }}>
      <Text size="xs" fw={700} c="red">
        {t('offerWorkspace.pricing.internalOnly')}
      </Text>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">{t('offerWorkspace.pricing.costBasis')}</Text>
        <Text size="sm">{offer.costBasis != null ? formatCurrencyChf(Number(offer.costBasis)) : '—'}</Text>
      </Group>
      <Group justify="space-between">
        <Text size="sm" fw={600}>{t('offerWorkspace.pricing.margin')}</Text>
        <Text size="sm" fw={700}>{offer.margin != null ? formatCurrencyChf(Number(offer.margin)) : '—'}</Text>
      </Group>
      {offer.discountAmount != null && Number(offer.discountAmount) > 0 && (
        <Group justify="space-between">
          <Text size="sm" c="dimmed">{t('offerWorkspace.pricing.discountAmount')}</Text>
          <Text size="sm">− {formatCurrencyChf(Number(offer.discountAmount))}</Text>
        </Group>
      )}
    </Stack>
  )

  return (
    <Modal opened={opened} onClose={onClose} title={t('offerWorkspace.generate.title')} size="xl">
      <Stack gap="md">
        {step === 'build' && (
          <Stack gap="sm" align="flex-start">
            <Text size="sm" c="dimmed">{t('offerWorkspace.generate.buildHint')}</Text>
            {buildError && <Alert color="red">{buildError}</Alert>}
            <Button loading={building} onClick={() => void build()}>
              {t('offerWorkspace.generate.build')}
            </Button>
          </Stack>
        )}

        {step === 'review' && (
          <Stack gap="sm">
            {!pdfUrl ? (
              <Loader size="sm" />
            ) : (
              <DocumentPreview
                src={pdfUrl}
                title={t('offerWorkspace.generate.previewTitle', { number: offer.offerNumber })}
                correspondenceLanguage={document?.correspondenceLanguage?.toUpperCase()}
                marginPanel={marginPanel}
              />
            )}
            {finalizeError && <Alert color="red">{finalizeError}</Alert>}
            <Group justify="space-between">
              <Button variant="default" onClick={() => setStep('build')}>
                {t('offerWorkspace.generate.regenerate')}
              </Button>
              <Button loading={finalizing} onClick={() => void confirm()}>
                {t('offerWorkspace.generate.confirm')}
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Modal>
  )
}
