// The kit ships no English of its own (KAN-364): a component's tests pass its
// labels in French, so a string that leaks past them — a forgotten default, a
// hardcoded aria-label — shows up as English text the tests then refuse.
import type { FilterBuilderLabels } from '../datagrid/FilterBuilder'
import type { ViewsAndFiltersLabels } from '../datagrid/ViewsAndFilters'
import type { RepeatableRowGroupLabels } from '../components/RepeatableRowGroup'

export const FR_FILTER_BUILDER_LABELS: FilterBuilderLabels = {
  addFilter: 'Nouveau filtre…',
  save: 'Enregistrer',
  cancel: 'Annuler',
  matchCount: (count) => `${count} résultats`,
  editFilter: 'Modifier le filtre',
  duplicateFilter: 'Dupliquer le filtre',
  deleteFilter: 'Supprimer le filtre',
}

export const FR_VIEWS_AND_FILTERS_LABELS: ViewsAndFiltersLabels = {
  viewsHeading: 'Vues',
  saveCurrentView: 'Enregistrer la vue actuelle sous…',
  savePlaceholder: 'Nom de la vue',
  manageFiltersHeading: 'Gérer les filtres',
  reset: 'Réinitialiser',
  setDefault: 'Définir par défaut',
  unsetDefault: 'Ne plus utiliser par défaut',
  deleteView: 'Supprimer la vue',
  filters: FR_FILTER_BUILDER_LABELS,
}

export const FR_REPEATABLE_ROW_LABELS: RepeatableRowGroupLabels = {
  primary: 'Principal',
  noLongerValid: 'Plus valable',
  doesNotWork: 'Ne fonctionne pas',
  doesNotWorkReasonPlaceholder: 'Motif',
  delete: 'Supprimer',
  consent: 'Consentement',
  consentScope: 'Portée',
  consentSource: 'Source',
  save: 'Enregistrer',
  cancel: 'Annuler',
  confirm: 'Confirmer',
  none: 'Aucun enregistré',
  genericError: 'Une erreur est survenue.',
  labelFieldPlaceholder: 'Libellé',
  rowActions: (label) => `Actions de la ligne ${label}`,
}

/** English words the kit used to ship as defaults; none may render now. */
export const FORMER_ENGLISH_DEFAULTS =
  /^(\+ New filter…|Save|Cancel|Views|Reset|Save current view as…|View name|Manage filters|Set as default|Remove as default|Delete view|Edit filter|Duplicate filter|Delete filter|Primary|No longer valid|Does not work|Delete|Consent|Scope|Confirm|None recorded|Label|Close|.* row actions|.* matching)$/
