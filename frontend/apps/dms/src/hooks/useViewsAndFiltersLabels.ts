import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CONDITIONS_BY_TYPE, type FilterCondition, type PredicateTextParts, type ViewsAndFiltersLabels } from '@nexotec/ui-kit'

const ALL_CONDITIONS: FilterCondition[] = [...new Set(Object.values(CONDITIONS_BY_TYPE).flat())]

/**
 * The views-and-filters panel's strings in the user's UI language, its
 * Filter Builder's included — every grid's `ViewsAndFilters` takes these, so
 * the kit ships no English of its own (KAN-364). The condition names and a
 * saved filter's text are one key per condition, so each language keeps its
 * own word order (KAN-415).
 */
export function useViewsAndFiltersLabels(): ViewsAndFiltersLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      viewsHeading: t('common.viewsAndFilters.viewsHeading'),
      saveCurrentView: t('common.viewsAndFilters.saveCurrentView'),
      savePlaceholder: t('common.viewsAndFilters.savePlaceholder'),
      confirmSaveView: t('common.viewsAndFilters.confirmSaveView'),
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
        conditions: Object.fromEntries(
          ALL_CONDITIONS.map((c) => [c, t(`common.filterBuilder.conditions.${c}`)])
        ) as Record<FilterCondition, string>,
        predicateText: Object.fromEntries(
          ALL_CONDITIONS.map((c) => [
            c,
            // `count` picks the plural form of the two N-days texts; the
            // other conditions have none and ignore it.
            ({ field, value, days }: PredicateTextParts) =>
              t(`common.filterBuilder.predicateText.${c}`, { field, value, days, count: days }),
          ])
        ) as Record<FilterCondition, (parts: PredicateTextParts) => string>,
        booleanTrue: (field: string) => t('common.filterBuilder.booleanTrue', { field }),
        booleanFalse: (field: string) => t('common.filterBuilder.booleanFalse', { field }),
      },
    }),
    [t]
  )
}
