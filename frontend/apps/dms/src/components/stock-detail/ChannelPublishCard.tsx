import { useState } from 'react'
import { Alert, Button } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog, OverviewCard, semantic, slate } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'
import { formatDateTime } from '../../utils/format'
import type { MarketplaceChannel, PublishingRead } from '../../api/types'

const CHANNEL_LABEL_KEY: Record<MarketplaceChannel, string> = {
  autoscout24: 'AutoScout24',
  carmarket: 'Carmarket',
  autolina: 'Autolina',
}

interface ChannelPublishCardProps {
  channel: MarketplaceChannel
  publishing: PublishingRead | undefined
  locale: string
  blocked: boolean
  onPublish: () => Promise<void>
  onUnpublish: () => Promise<void>
}

/** § Publishing tab — one card per channel, matching the live reference
 * prototype exactly. Unpublish is a confirmed destructive action
 * (AS24i's own full-delivery semantics: an unpublish DELETES the
 * listing, its stats and its URL at the marketplace) — never a plain
 * toggle switch.
 *
 * KAN-27 (PR 2/2) — `state` (the dealer's own intent) and
 * `transmissionStatus` (whether the last delivery attempt to the
 * marketplace actually succeeded) are independent facts; showing
 * "Published" the instant `state` flips is exactly the fake-instant-
 * success flip PR 1/2's own transmission work exists to replace.
 * `state === 'published'` still drives which action is offered
 * (Publish vs. the confirmed-destructive Unpublish) — that is the
 * dealer's intent, unaffected by whether a delivery attempt succeeded —
 * but the STATUS LINE and its colour now come from `transmissionStatus`.
 *
 * KAN-272 (ADR-035): a refused publish or unpublish says so — on the card
 * for a publish, inside the still-open confirmation for an unpublish — so
 * the retry, under the same Idempotency-Key, is one click away.
 */
export function ChannelPublishCard({ channel, publishing, locale, blocked, onPublish, onUnpublish }: ChannelPublishCardProps) {
  const { t } = useTranslation()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [publishPending, setPublishPending] = useState(false)
  const [publishError, setPublishError] = useState<string | null>(null)
  const [unpublishError, setUnpublishError] = useState<string | null>(null)
  const isPublished = publishing?.state === 'published'
  const transmissionStatus = publishing?.transmissionStatus

  const publish = async () => {
    setPublishPending(true)
    setPublishError(null)
    try {
      await onPublish()
    } catch (err) {
      setPublishError(err instanceof ApiError ? err.message : t('stockDetail.publishing.publishError'))
    } finally {
      setPublishPending(false)
    }
  }

  const openUnpublish = () => {
    setUnpublishError(null)
    setConfirmOpen(true)
  }

  const confirmUnpublish = async () => {
    setSubmitting(true)
    setUnpublishError(null)
    try {
      await onUnpublish()
      setConfirmOpen(false)
    } catch (err) {
      setUnpublishError(err instanceof ApiError ? err.message : t('stockDetail.publishing.unpublishError'))
    } finally {
      setSubmitting(false)
    }
  }

  const statusLabel = !isPublished
    ? t('stockDetail.publishing.notPublished')
    : transmissionStatus === 'transmitted'
      ? t('stockDetail.publishing.published')
      : transmissionStatus === 'failed'
        ? t('stockDetail.publishing.transmissionFailed')
        : t('stockDetail.publishing.transmissionPending')
  const statusColor = !isPublished
    ? undefined
    : transmissionStatus === 'transmitted'
      ? semantic.success.text
      : transmissionStatus === 'failed'
        ? semantic.destructive.text
        : semantic.warning.text

  return (
    <OverviewCard title={CHANNEL_LABEL_KEY[channel]}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ color: statusColor, fontWeight: 600, fontSize: 13 }}>{statusLabel}</span>
        {isPublished && transmissionStatus === 'failed' && publishing?.lastTransmissionError && (
          <span style={{ fontSize: 12, color: semantic.destructive.text }}>{publishing.lastTransmissionError}</span>
        )}
        <span style={{ fontSize: 12, color: slate[5] }}>
          {publishing?.lastPublishedAt
            ? t('stockDetail.publishing.lastPublishedAt', { date: formatDateTime(publishing.lastPublishedAt, locale) })
            : t('stockDetail.publishing.neverPublished')}
        </span>
        {isPublished ? (
          <Button variant="default" color="red" onClick={openUnpublish}>
            {t('stockDetail.publishing.unpublish', { channel: CHANNEL_LABEL_KEY[channel] })}
          </Button>
        ) : (
          <Button onClick={() => void publish()} disabled={blocked} loading={publishPending}>
            {t('stockDetail.publishing.publishTo', { channel: CHANNEL_LABEL_KEY[channel] })}
          </Button>
        )}
        {publishError && !isPublished && (
          <Alert color="red" role="alert">
            {publishError}
          </Alert>
        )}
      </div>

      <FormDialog
        opened={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={t('stockDetail.publishing.unpublishConfirmTitle')}
        onSubmit={confirmUnpublish}
        submitLabel={t('stockDetail.publishing.unpublishConfirmSubmit')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
      >
        <p>{t('stockDetail.publishing.unpublishConfirmBody', { channel: CHANNEL_LABEL_KEY[channel] })}</p>
        {unpublishError && (
          <Alert color="red" role="alert">
            {unpublishError}
          </Alert>
        )}
      </FormDialog>
    </OverviewCard>
  )
}
