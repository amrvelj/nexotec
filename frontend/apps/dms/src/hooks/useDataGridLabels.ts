import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { DataGridLabels } from '@nexotec/ui-kit'

/**
 * The DataGrid's own strings (footer count, loading-more, retry, the row
 * menu's trigger) in the user's UI language — every grid takes these, so
 * the kit ships no English of its own (KAN-422). A grid with a selection
 * names its checkboxes on the `selection` prop itself.
 */
export function useDataGridLabels(): DataGridLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      showing: (count: number) => t('common.showing', { count }),
      showingOfTotal: (count: number, total: string) => t('common.showingOfTotal', { count, total }),
      loadingMore: t('common.loadingMore'),
      retry: t('common.retry'),
      rowActionsLabel: t('common.rowActionsLabel'),
    }),
    [t]
  )
}
