import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { ViewsAndFiltersLabels } from '@nexotec/ui-kit'

/**
 * The views-and-filters panel's strings in the user's UI language, its
 * Filter Builder's included — every grid's `ViewsAndFilters` takes these, so
 * the kit ships no English of its own (KAN-364).
 */
export function useViewsAndFiltersLabels(): ViewsAndFiltersLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      viewsHeading: t('common.viewsAndFilters.viewsHeading'),
      saveCurrentView: t('common.viewsAndFilters.saveCurrentView'),
      savePlaceholder: t('common.viewsAndFilters.savePlaceholder'),
      manageFiltersHeading: t('common.viewsAndFilters.manageFiltersHeading'),
      reset: t('common.viewsAndFilters.reset'),
      setDefault: t('common.viewsAndFilters.setDefault'),
      unsetDefault: t('common.viewsAndFilters.unsetDefault'),
      deleteView: t('common.viewsAndFilters.deleteView'),
      filters: {
        addFilter: t('common.filterBuilder.addFilter'),
        save: t('common.save'),
        cancel: t('common.cancel'),
        matchCount: (count: number) => t('common.filterBuilder.matchCount', { count }),
        editFilter: t('common.filterBuilder.editFilter'),
        duplicateFilter: t('common.filterBuilder.duplicateFilter'),
        deleteFilter: t('common.filterBuilder.deleteFilter'),
      },
    }),
    [t]
  )
}
