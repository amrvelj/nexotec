import { AlertTriangle } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { radius, semantic, spacing } from '@nexotec/ui-kit'
import type { BlockingCondition } from '../../api/types'

// The marketplace fields the API names a blocking condition by
// (inventory/services/publishing.py). The API's sentence is German only, so
// the screen says it from the field, in the UI language (KAN-437); a field
// added there without a sentence here is the generic one.
const BLOCKING_FIELDS = new Set(['Lagerstatus', 'Bilder', 'Preis', 'Aussenfarbe', 'Aufbau', 'Kilometer', 'InvSetzJahr', 'Energieetikette'])

function blockingConditionText(t: TFunction, field: string): string {
  return t(`stockDetail.publishing.blockingCondition.${BLOCKING_FIELDS.has(field) ? field : 'other'}`)
}

/**
 * § Publishing tab — blocking conditions are computed and NAMED BEFORE
 * SEND using the marketplace's own field name (ADR-062), shown as it is.
 * Confirmed verbatim against the live reference prototype's own banner.
 */
export function BlockingConditionBanner({ conditions }: { conditions: BlockingCondition[] }) {
  const { t } = useTranslation()
  if (conditions.length === 0) return null

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: spacing.xs,
        padding: spacing.md,
        borderRadius: radius.md,
        backgroundColor: semantic.destructive.surface,
        border: `1px solid ${semantic.destructive.border}`,
        color: semantic.destructive.text,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: spacing.xs, fontWeight: 600 }}>
        <AlertTriangle size={16} />
        <span>{t('stockDetail.publishing.blockingBannerTitle', { count: conditions.length })}</span>
      </div>
      <ul style={{ margin: 0, paddingLeft: 20 }}>
        {conditions.map((c) => (
          <li key={c.field}>
            <strong>{c.field}</strong> — {blockingConditionText(t, c.field)}
          </li>
        ))}
      </ul>
    </div>
  )
}
