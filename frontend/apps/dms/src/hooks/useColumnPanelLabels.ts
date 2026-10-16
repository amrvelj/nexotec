import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnConfigPanelLabels } from '@nexotec/ui-kit'

/**
 * The Columns panel's strings in the user's UI language — every grid's
 * `ColumnConfigPanel` takes these, so the kit ships no English of its own
 * (KAN-362).
 */
export function useColumnPanelLabels(): ColumnConfigPanelLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      trigger: t('common.columnPanel.trigger'),
      search: t('common.columnPanel.search'),
      reset: t('common.columnPanel.reset'),
      presets: t('common.columnPanel.presets'),
      lockedTooltip: t('common.columnPanel.lockedTooltip'),
      pin: t('common.columnPanel.pin'),
      unpin: t('common.columnPanel.unpin'),
      moveUp: (label: string) => t('common.columnPanel.moveUp', { label }),
      moveDown: (label: string) => t('common.columnPanel.moveDown', { label }),
    }),
    [t]
  )
}
