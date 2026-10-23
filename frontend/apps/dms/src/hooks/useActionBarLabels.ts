import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { ActionBarLabels } from '@nexotec/ui-kit'

/**
 * The Action Bar's own strings (density cycle, refresh) in the user's UI
 * language — every grid's `ActionBar` takes these, so the kit ships no
 * English of its own (KAN-364).
 */
export function useActionBarLabels(): ActionBarLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      density: {
        compact: t('common.density.compact'),
        default: t('common.density.default'),
        comfortable: t('common.density.comfortable'),
      },
      densityTooltip: (label: string) => t('common.density.tooltip', { label }),
      densityAriaLabel: t('common.density.ariaLabel'),
      refresh: t('common.refresh'),
    }),
    [t]
  )
}
