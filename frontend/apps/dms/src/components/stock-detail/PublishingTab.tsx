import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SegmentedControl } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { CounterTextarea, MediaGallery, OverviewCard, semantic, slate, spacing } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { BlockingConditionBanner } from './BlockingConditionBanner'
import { ChannelPublishCard } from './ChannelPublishCard'
import type { MarketplaceChannel, MediaRead, PublishingRead } from '../../api/types'

const CHANNELS: MarketplaceChannel[] = ['autoscout24', 'carmarket', 'autolina']

// § ADR-062 — per-channel title DISPLAY limits (the stored field itself
// allows up to 500 characters; only this many render on the channel's
// own results list).
const TITLE_DISPLAY_LIMITS: Record<MarketplaceChannel, number> = { autoscout24: 125, carmarket: 80, autolina: 100 }
const CHANNEL_LABELS: Record<MarketplaceChannel, string> = {
  autoscout24: 'AutoScout24',
  carmarket: 'Carmarket',
  autolina: 'Autolina',
}

interface EquipmentRead {
  ausstattungCodes: string[]
  extras: string[]
  eigenschaften: string[]
  providerAusstattung: Record<string, string>
}

interface PublishingTabProps {
  stockItemId: string
  locale: string
}

/**
 * § Publishing tab (ADR-062). One shared blocking-condition banner above
 * three per-channel cards (confirmed against the live reference
 * prototype), a channel-selectable listing-text editor (the SAME text
 * fields can differ per channel — the model is per (stockItem, channel)
 * — the prototype's own single "DE" language tab is language, not
 * channel, but the identical selector idiom applies here for channel),
 * a shared MediaGallery (pictures are item-level, not per-channel), and
 * a read-only equipment summary sourced from app.vehicle (equipment is a
 * fact about the car, per ADR-062's own three-concepts rule).
 */
export function PublishingTab({ stockItemId, locale }: PublishingTabProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [activeChannel, setActiveChannel] = useState<MarketplaceChannel>('autoscout24')

  // Three explicit calls, not a .map() over CHANNELS — a fixed count of
  // hooks called in a fixed order, the safe form of "one query per
  // channel" rather than relying on CHANNELS never changing shape.
  const autoscout24Query = useQuery({
    queryKey: ['stock-item', stockItemId, 'publishing', 'autoscout24'],
    queryFn: () => api.get<PublishingRead>(`/inventory/stock-items/${stockItemId}/publishing/autoscout24`),
  })
  const carmarketQuery = useQuery({
    queryKey: ['stock-item', stockItemId, 'publishing', 'carmarket'],
    queryFn: () => api.get<PublishingRead>(`/inventory/stock-items/${stockItemId}/publishing/carmarket`),
  })
  const autolinaQuery = useQuery({
    queryKey: ['stock-item', stockItemId, 'publishing', 'autolina'],
    queryFn: () => api.get<PublishingRead>(`/inventory/stock-items/${stockItemId}/publishing/autolina`),
  })
  const publishingByChannel: Partial<Record<MarketplaceChannel, PublishingRead>> = {
    ...(autoscout24Query.data ? { autoscout24: autoscout24Query.data } : {}),
    ...(carmarketQuery.data ? { carmarket: carmarketQuery.data } : {}),
    ...(autolinaQuery.data ? { autolina: autolinaQuery.data } : {}),
  }

  const mediaQuery = useQuery({
    queryKey: ['stock-item', stockItemId, 'media'],
    queryFn: () => api.get<MediaRead[]>(`/inventory/stock-items/${stockItemId}/media`),
  })
  const equipmentQuery = useQuery({
    queryKey: ['stock-item', stockItemId, 'equipment'],
    queryFn: () => api.get<EquipmentRead>(`/inventory/stock-items/${stockItemId}/equipment`),
  })

  const invalidatePublishing = (channel: MarketplaceChannel) =>
    queryClient.invalidateQueries({ queryKey: ['stock-item', stockItemId, 'publishing', channel] })
  const invalidateMedia = () => queryClient.invalidateQueries({ queryKey: ['stock-item', stockItemId, 'media'] })

  const activePublishing = publishingByChannel[activeChannel]
  // Blocking conditions are item-level, not per-channel (compute_blocking_
  // conditions ignores channel entirely) — any loaded channel's own copy
  // is the same list, so the first one that's arrived is fine. Cheap
  // enough to skip memoizing.
  const blockingConditions = Object.values(publishingByChannel)[0]?.blockingConditions ?? []

  const saveListingText = async (patch: Record<string, unknown>) => {
    await api.patch(`/inventory/stock-items/${stockItemId}/publishing/${activeChannel}`, patch)
    void invalidatePublishing(activeChannel)
  }

  const publishChannel = async (channel: MarketplaceChannel) => {
    await api.post(`/inventory/stock-items/${stockItemId}/publishing/${channel}/publish`)
    void invalidatePublishing(channel)
  }

  const unpublishChannel = async (channel: MarketplaceChannel) => {
    await api.post(`/inventory/stock-items/${stockItemId}/publishing/${channel}/unpublish`, { confirm: true })
    void invalidatePublishing(channel)
  }

  const mediaItems = (mediaQuery.data ?? []).map((m) => ({ id: m.id, url: m.url, position: m.position }))

  const addMedia = async () => {
    const url = window.prompt(t('stockDetail.publishing.media.addPrompt'))
    if (!url) return
    await api.post(`/inventory/stock-items/${stockItemId}/media`, { url })
    void invalidateMedia()
  }
  const removeMedia = async (mediaId: string) => {
    await api.delete(`/inventory/stock-items/${stockItemId}/media/${mediaId}`)
    void invalidateMedia()
  }
  const reorderMedia = async (orderedIds: string[]) => {
    await api.post(`/inventory/stock-items/${stockItemId}/media/reorder`, { orderedMediaIds: orderedIds })
    void invalidateMedia()
  }

  const equipment = equipmentQuery.data

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.lg }}>
      <BlockingConditionBanner conditions={blockingConditions} />

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: slate[7], marginBottom: spacing.sm }}>
          {t('stockDetail.publishing.channelsTitle')}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: spacing.md }}>
          {CHANNELS.map((channel) => (
            <ChannelPublishCard
              key={channel}
              channel={channel}
              publishing={publishingByChannel[channel]}
              locale={locale}
              blocked={blockingConditions.length > 0}
              onPublish={() => publishChannel(channel)}
              onUnpublish={() => unpublishChannel(channel)}
            />
          ))}
        </div>
      </div>

      <OverviewCard title={t('stockDetail.publishing.listingTextTitle')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.md }}>
          <SegmentedControl
            value={activeChannel}
            onChange={(v) => setActiveChannel(v as MarketplaceChannel)}
            data={CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABELS[c] }))}
          />
          <CounterTextarea
            label={t('stockDetail.publishing.fields.zusatztitel')}
            value={activePublishing?.zusatztitel ?? ''}
            onChange={(v) => saveListingText({ zusatztitel: v })}
            maxLength={500}
            displayLimitCaption={t('stockDetail.publishing.titleDisplayLimit', {
              count: TITLE_DISPLAY_LIMITS[activeChannel],
            })}
            minRows={1}
          />
          <CounterTextarea
            label={t('stockDetail.publishing.fields.bemerkungen')}
            value={activePublishing?.bemerkungen ?? ''}
            onChange={(v) => saveListingText({ bemerkungen: v })}
            maxLength={4000}
          />
          <CounterTextarea
            label={t('stockDetail.publishing.fields.zustandsbeschreibung')}
            value={activePublishing?.zustandsbeschreibung ?? ''}
            onChange={(v) => saveListingText({ zustandsbeschreibung: v })}
            maxLength={2000}
          />
          <CounterTextarea
            label={t('stockDetail.publishing.fields.haendlerbemerkungen')}
            value={activePublishing?.haendlerbemerkungen ?? ''}
            onChange={(v) => saveListingText({ haendlerbemerkungen: v })}
            maxLength={2000}
            description={t('stockDetail.publishing.haendlerbemerkungenHint')}
          />
        </div>
      </OverviewCard>

      <OverviewCard title={t('stockDetail.publishing.media.title')}>
        <MediaGallery
          items={mediaItems}
          maxItems={16}
          onAdd={addMedia}
          onRemove={removeMedia}
          onReorder={reorderMedia}
          labels={{
            mainImage: t('stockDetail.publishing.media.mainImage'),
            moveLeft: t('stockDetail.publishing.media.moveLeft'),
            moveRight: t('stockDetail.publishing.media.moveRight'),
            remove: t('stockDetail.publishing.media.remove'),
            addPhoto: t('stockDetail.publishing.media.addPhoto'),
            countOf: (count, max) => t('stockDetail.publishing.media.countOf', { count, max }),
          }}
        />
      </OverviewCard>

      {equipment && (
        <OverviewCard title={t('stockDetail.publishing.equipment.title')}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.md }}>
            <EquipmentList label={t('stockDetail.publishing.equipment.ausstattung')} items={equipment.ausstattungCodes} />
            <EquipmentList label={t('stockDetail.publishing.equipment.extras')} items={equipment.extras} />
            <EquipmentList label={t('stockDetail.publishing.equipment.eigenschaften')} items={equipment.eigenschaften} />
          </div>
          <p style={{ fontSize: 12, color: slate[5], marginTop: spacing.sm }}>
            {t('stockDetail.publishing.equipment.hint')}
          </p>
        </OverviewCard>
      )}
    </div>
  )
}

function EquipmentList({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, color: slate[6], textTransform: 'uppercase', marginBottom: 4 }}>
        {label}
      </div>
      {items.length === 0 ? (
        <span style={{ fontSize: 13, color: slate[4] }}>—</span>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {items.map((item) => (
            <span
              key={item}
              style={{
                fontSize: 12,
                padding: '2px 8px',
                borderRadius: 9999,
                backgroundColor: semantic.informational.surface,
                color: semantic.informational.text,
              }}
            >
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
