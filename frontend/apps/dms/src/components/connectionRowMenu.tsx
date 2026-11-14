import { Ban, Gauge, KeyRound, MapPin, Play, RefreshCw, Trash2, Zap } from 'lucide-react'
import type { RowMenuGroups } from '@nexotec/ui-kit'
import type { IntegrationConnectionRead } from '../api/types'

export interface ConnectionRowMenuActions {
  onTest: () => void
  onViewUsage: () => void
  onRotateSecret: () => void
  onToggleEnabled: () => void
  onDelete: () => void
  /** KAN-222 — «Standort zuordnen»; offered only where the connection's accounts are per site. */
  onBindLocation?: () => void
}

/**
 * The account at the provider, as the card and the delete confirmation name
 * it (KAN-222, FR-A-20): the API's `accountIdentifier`, derived by each
 * provider from the connection's non-secret configuration
 * (`app/integration/services/account_identifier.py`). Missing → «Kennung
 * fehlt», never a blank or a dash that reads as «no account».
 */
export function connectionAccountIdentifier(
  t: (key: string) => string,
  connection: Pick<IntegrationConnectionRead, 'accountIdentifier'>
): string {
  return connection.accountIdentifier ?? t('integrationsList.accountIdentifierMissing')
}

/**
 * The secret slots a connection's secret dialog offers (KAN-603): every slot
 * its provider requires, set or not, plus any slot already stored. A
 * connection whose first secret write failed has no stored slot and must
 * still be repairable from its card.
 */
export function settableSecretSlots(
  connection: Pick<IntegrationConnectionRead, 'secretSlots'>,
  requiredSecretSlots: readonly string[] = []
): string[] {
  const stored: string[] = (connection.secretSlots ?? []).map((s) => s.slot)
  return [...new Set([...requiredSecretSlots, ...stored])]
}

/**
 * WP-6 PR-7 (ADR-061) — "a connection is an entity like any other; its
 * screen is not special." No dedicated connection detail screen exists
 * in this PR's own scope (cards on the dealer side, a grid row on the
 * platform side — never a drill-in page), so this returns a plain
 * `RowMenuGroups` rather than a full primary/alternative/overflow triple:
 * the ONE function both surfaces call, so Test/Secret/Disable/Enable/Delete
 * never carry a different enabled state or reason on the two of them.
 * No "Connect" action here — that's a create flow (a brand-new
 * connection has no row yet), never a row-menu item on an existing one.
 *
 * KAN-603 — the secret item is enabled whenever the provider uses a secret
 * slot (`requiredSecretSlots`), labelled «Secret setzen» while none is
 * stored and «Secret erneuern» once one is; «Verbindung löschen» is the
 * destructive item, behind a confirmation (`DeleteConnectionModal`).
 */
export function buildConnectionRowMenu(
  t: (key: string, options?: Record<string, unknown>) => string,
  connection: Pick<IntegrationConnectionRead, 'enabled' | 'secretSlots'> &
    Partial<Pick<IntegrationConnectionRead, 'locationBindable'>>,
  actions: ConnectionRowMenuActions,
  requiredSecretSlots?: readonly string[]
): RowMenuGroups {
  const hasStoredSecret = (connection.secretSlots ?? []).length > 0
  const hasSettableSlot = settableSecretSlots(connection, requiredSecretSlots).length > 0

  return {
    edit: [
      { label: t('integrationsList.actions.test'), icon: <Zap size={16} />, onClick: actions.onTest },
      { label: t('integrationsList.actions.viewUsage'), icon: <Gauge size={16} />, onClick: actions.onViewUsage },
      {
        label: hasStoredSecret ? t('integrationsList.actions.rotateSecret') : t('integrationsList.actions.setSecret'),
        icon: hasStoredSecret ? <RefreshCw size={16} /> : <KeyRound size={16} />,
        onClick: actions.onRotateSecret,
        disabled: !hasSettableSlot,
        disabledReason: !hasSettableSlot ? t('integrationsList.actions.rotateSecretDisabledReason') : undefined,
      },
      ...(connection.locationBindable && actions.onBindLocation
        ? [{ label: t('integrationsList.actions.bindLocation'), icon: <MapPin size={16} />, onClick: actions.onBindLocation }]
        : []),
      ...(connection.enabled
        ? []
        : [{ label: t('integrationsList.actions.enable'), icon: <Play size={16} />, onClick: actions.onToggleEnabled }]),
    ],
    destructive: [
      ...(connection.enabled
        ? [{ label: t('integrationsList.actions.disable'), icon: <Ban size={16} />, onClick: actions.onToggleEnabled }]
        : []),
      { label: t('integrationsList.actions.delete'), icon: <Trash2 size={16} />, onClick: actions.onDelete },
    ],
  }
}
