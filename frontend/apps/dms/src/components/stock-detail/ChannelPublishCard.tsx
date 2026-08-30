import { useState } from 'react'
import { Button } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog, OverviewCard, semantic, slate } from '@nexotec/ui-kit'
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
 */
export function ChannelPublishCard({ channel, publishing, locale, blocked, onPublish, onUnpublish }: ChannelPublishCardProps) {
  const { t } = useTranslation()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const isPublished = publishing?.state === 'published'

  const confirmUnpublish = async () => {
    setSubmitting(true)
    try {
      await onUnpublish()
      setConfirmOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <OverviewCard title={CHANNEL_LABEL_KEY[channel]}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ color: isPublished ? semantic.success.text : undefined, fontWeight: 600, fontSize: 13 }}>
          {isPublished ? t('stockDetail.publishing.published') : t('stockDetail.publishing.notPublished')}
        </span>
        <span style={{ fontSize: 12, color: slate[5] }}>
          {publishing?.lastPublishedAt
            ? t('stockDetail.publishing.lastPublishedAt', { date: formatDateTime(publishing.lastPublishedAt, locale) })
            : t('stockDetail.publishing.neverPublished')}
        </span>
        {isPublished ? (
          <Button variant="default" color="red" onClick={() => setConfirmOpen(true)}>
            {t('stockDetail.publishing.unpublish', { channel: CHANNEL_LABEL_KEY[channel] })}
          </Button>
        ) : (
          <Button onClick={onPublish} disabled={blocked}>
            {t('stockDetail.publishing.publishTo', { channel: CHANNEL_LABEL_KEY[channel] })}
          </Button>
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
      </FormDialog>
    </OverviewCard>
  )
}
