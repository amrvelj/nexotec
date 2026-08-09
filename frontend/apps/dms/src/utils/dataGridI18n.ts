import type { TFunction } from 'i18next'

// Shared translated-labels builder for ui-kit's DataGrid `labels` prop —
// every tab that embeds a DataGrid needs the same handful of strings
// (footer count, retry, loading-more, row-actions trigger), so this is
// the one place they're built rather than four near-identical literals.
export function dataGridLabels(t: TFunction) {
  return {
    showing: (count: number) => t('common.showing', { count }),
    showingOfTotal: (count: number, total: string) => t('common.showingOfTotal', { count, total }),
    loadingMore: t('common.loadingMore'),
    retry: t('common.retry'),
    rowActionsLabel: t('common.rowActionsLabel'),
  }
}
