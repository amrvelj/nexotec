// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { ChannelPublishCard } from './ChannelPublishCard'
import type { PublishingRead } from '../../api/types'

// KAN-27 (PR 2/2) — `state` (intent) and `transmissionStatus` (whether
// the last delivery attempt actually succeeded) are independent; the
// card's status line must reflect transmissionStatus, never flip to
// "Published" the instant state does. This is the fix for the exact
// fake-instant-success flip PR 1/2's own backend work exists to replace.

function publishing(over: Partial<PublishingRead> = {}): PublishingRead {
  return {
    id: 'p1',
    stockItemId: 's1',
    channel: 'autoscout24',
    state: 'published',
    zusatztitel: null,
    bemerkungen: null,
    zustandsbeschreibung: null,
    haendlerbemerkungen: null,
    youtubeUrl: null,
    pdfDocumentRef: null,
    lastPublishedAt: '2026-09-16T10:00:00Z',
    transmissionStatus: 'transmitted',
    lastTransmissionError: null,
    lastAttemptedAt: '2026-09-16T10:00:05Z',
    blockingConditions: [],
    version: 1,
    ...over,
  }
}

function renderCard(publishingRow: PublishingRead | undefined, overrides: Partial<Parameters<typeof ChannelPublishCard>[0]> = {}) {
  render(
    <MantineProvider theme={theme}>
      <ChannelPublishCard
        channel="autoscout24"
        publishing={publishingRow}
        locale="de"
        blocked={false}
        onPublish={vi.fn()}
        onUnpublish={vi.fn()}
        {...overrides}
      />
    </MantineProvider>,
  )
}

describe('ChannelPublishCard', () => {
  it('shows "not published" when there is no publishing row yet', () => {
    renderCard(undefined)
    expect(screen.getByText(i18n.t('stockDetail.publishing.notPublished'))).toBeInTheDocument()
  })

  it('shows "not published" once unpublished, regardless of the last transmission outcome', () => {
    renderCard(publishing({ state: 'not_published', transmissionStatus: 'transmitted' }))
    expect(screen.getByText(i18n.t('stockDetail.publishing.notPublished'))).toBeInTheDocument()
  })

  it('shows "Published" only once transmissionStatus is transmitted, never on intent alone', () => {
    renderCard(publishing({ state: 'published', transmissionStatus: 'transmitted' }))
    expect(screen.getByText(i18n.t('stockDetail.publishing.published'))).toBeInTheDocument()
  })

  it('shows a pending state while the outbox has not yet attempted transmission', () => {
    renderCard(publishing({ state: 'published', transmissionStatus: 'pending' }))
    expect(screen.getByText(i18n.t('stockDetail.publishing.transmissionPending'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockDetail.publishing.published'))).not.toBeInTheDocument()
  })

  it('shows a failed state with the actual error message when transmission failed', () => {
    renderCard(
      publishing({
        state: 'published',
        transmissionStatus: 'failed',
        lastTransmissionError: 'No autoscout24 connection configured for this dealership.',
      }),
    )
    expect(screen.getByText(i18n.t('stockDetail.publishing.transmissionFailed'))).toBeInTheDocument()
    expect(screen.getByText('No autoscout24 connection configured for this dealership.')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('stockDetail.publishing.published'))).not.toBeInTheDocument()
  })

  it('never shows the error message once unpublished, even if the row still carries a stale one', () => {
    renderCard(
      publishing({ state: 'not_published', transmissionStatus: 'failed', lastTransmissionError: 'stale error' }),
    )
    expect(screen.queryByText('stale error')).not.toBeInTheDocument()
  })

  it('offers Publish when not published, and the confirmed-destructive Unpublish once published — regardless of transmissionStatus', () => {
    renderCard(publishing({ state: 'published', transmissionStatus: 'failed' }))
    expect(
      screen.getByRole('button', { name: i18n.t('stockDetail.publishing.unpublish', { channel: 'AutoScout24' }) }),
    ).toBeInTheDocument()
  })
})
