import { Ban, Gauge, Play, RefreshCw, Zap } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { IntegrationConnectionRead } from '../api/types'

export interface ConnectionRowMenuActions {
  onTest: () => void
  onViewUsage: () => void
  onRotateSecret: () => void
  onToggleEnabled: () => void
}

/**
 * WP-6 PR-7 (ADR-061) — "a connection is an entity like any other; its
 * screen is not special." No dedicated connection detail screen exists
 * in this PR's own scope (cards on the dealer side, a grid row on the
 * platform side — never a drill-in page), so this returns a plain
 * `RowMenuGroups` rather than a full primary/alternative/overflow triple:
 * the ONE function both surfaces call, so Test/Rotate/Disable/Enable
 * never carry a different enabled state or reason on the two of them.
 * No "Connect" action here — that's a create flow (a brand-new
 * connection has no row yet), never a row-menu item on an existing one.
 */
export function buildConnectionRowMenu(
  t: (key: string, options?: Record<string, unknown>) => string,
  connection: Pick<IntegrationConnectionRead, 'enabled' | 'secretSlots'>,
  actions: ConnectionRowMenuActions
): RowMenuGroups {
  const hasSecrets = (connection.secretSlots ?? []).length > 0

  return {
    edit: [
      { label: t('integrationsList.actions.test'), icon: <Zap size={16} />, onClick: actions.onTest },
      { label: t('integrationsList.actions.viewUsage'), icon: <Gauge size={16} />, onClick: actions.onViewUsage },
      {
        label: t('integrationsList.actions.rotateSecret'),
        icon: <RefreshCw size={16} />,
        onClick: actions.onRotateSecret,
        disabled: !hasSecrets,
        disabledReason: !hasSecrets ? t('integrationsList.actions.rotateSecretDisabledReason') : undefined,
      },
      ...(connection.enabled
        ? []
        : [{ label: t('integrationsList.actions.enable'), icon: <Play size={16} />, onClick: actions.onToggleEnabled }]),
    ],
    destructive: connection.enabled
      ? [{ label: t('integrationsList.actions.disable'), icon: <Ban size={16} />, onClick: actions.onToggleEnabled }]
      : [],
  }
}
