// The kit ships no English of its own (KAN-364): a component's tests pass its
// labels in French, so a string that leaks past them — a forgotten default, a
// hardcoded aria-label — shows up as English text the tests then refuse.
import type { FilterBuilderLabels } from '../datagrid/FilterBuilder'
import type { ViewsAndFiltersLabels } from '../datagrid/ViewsAndFilters'
import type { RepeatableRowGroupLabels } from '../components/RepeatableRowGroup'
import type { ActionBarLabels } from '../datagrid/ActionBar'
import type { FilterConditionLabels } from '../datagrid/filterPredicate'
import type { DataGridLabels } from '../datagrid/DataGrid'

// One template per condition, word order included (KAN-415).
export const FR_CONDITION_LABELS: FilterConditionLabels = {
  conditions: {
    contains: 'contient',
    equals: 'est exactement',
    notEquals: "n'est pas",
    is: 'est',
    isNot: "n'est pas",
    greaterThan: 'supérieur à',
    lessThan: 'inférieur à',
    atLeast: 'au moins',
    atMost: 'au plus',
    today: "aujourd'hui",
    thisWeek: 'cette semaine',
    thisMonth: 'ce mois-ci',
    thisYear: 'cette année',
    inTheLastDays: 'au cours des N derniers jours',
    moreThanDaysAgo: 'il y a plus de N jours',
  },
  predicateText: {
    contains: ({ field, value }) => `${field} : contient ${value}`,
    equals: ({ field, value }) => `${field} : exactement ${value}`,
    notEquals: ({ field, value }) => `${field} : sauf ${value}`,
    is: ({ field, value }) => `${field} : ${value}`,
    isNot: ({ field, value }) => `${field} : sauf ${value}`,
    greaterThan: ({ field, value }) => `${field} : plus de ${value}`,
    lessThan: ({ field, value }) => `${field} : moins de ${value}`,
    atLeast: ({ field, value }) => `${field} : au moins ${value}`,
    atMost: ({ field, value }) => `${field} : au plus ${value}`,
    today: ({ field }) => `${field} : aujourd'hui`,
    thisWeek: ({ field }) => `${field} : cette semaine`,
    thisMonth: ({ field }) => `${field} : ce mois-ci`,
    thisYear: ({ field }) => `${field} : cette année`,
    inTheLastDays: ({ field, days }) => `${field} : au cours des ${days} derniers jours`,
    moreThanDaysAgo: ({ field, days }) => `${field} : il y a plus de ${days} jours`,
  },
  booleanTrue: (field) => `${field} : oui`,
  booleanFalse: (field) => `${field} : non`,
}

export const FR_FILTER_BUILDER_LABELS: FilterBuilderLabels = {
  addFilter: 'Nouveau filtre…',
  save: 'Enregistrer',
  cancel: 'Annuler',
  matchCount: (count) => `${count} résultats`,
  editFilter: 'Modifier le filtre',
  deleteFilter: 'Supprimer le filtre',
  dayCount: 'Nombre de jours',
  invalidDayCount: (min, max) => `Saisissez un nombre entier de jours entre ${min} et ${max}.`,
  ...FR_CONDITION_LABELS,
}

export const FR_VIEWS_AND_FILTERS_LABELS: ViewsAndFiltersLabels = {
  viewsHeading: 'Vues',
  saveCurrentView: 'Enregistrer la vue actuelle sous…',
  savePlaceholder: 'Nom de la vue',
  confirmSaveView: 'Enregistrer la vue',
  manageFiltersHeading: 'Gérer les filtres',
  reset: 'Réinitialiser',
  setDefault: 'Définir par défaut',
  unsetDefault: 'Ne plus utiliser par défaut',
  deleteView: 'Supprimer la vue',
  viewChanged: 'Modifiée par rapport à la vue enregistrée',
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

export const FR_ACTION_BAR_LABELS: ActionBarLabels = {
  density: { compact: 'Compacte', default: 'Standard', comfortable: 'Confortable' },
  densityTooltip: (label) => `Densité : ${label}`,
  densityAriaLabel: 'Changer la densité des lignes',
  refresh: 'Actualiser',
}

export const FR_DATA_GRID_LABELS: DataGridLabels = {
  showing: (shown) => `${shown} affichés`,
  showingOfTotal: (shown, total) => `${shown} affichés sur ${total}`,
  loadingMore: 'Chargement…',
  retry: 'Réessayer',
  rowActionsLabel: 'Actions de la ligne',
}

export const FR_WIZARD_LABELS = {
  nextLabel: 'Suivant',
  submitLabel: 'Créer',
  backLabel: 'Retour',
  cancelLabel: 'Annuler',
}

export const FR_GLOBAL_SEARCH_LABELS = {
  placeholder: 'Rechercher…',
  recentsLabel: 'Récents',
  noResultsLabel: 'Aucun résultat',
  errorLabel: 'La recherche a échoué — réessayez.',
}

/** English words the kit used to ship as defaults — the panel's strings
 * (KAN-364), the Filter Builder's condition names and saved-filter text
 * (KAN-415), the Action Bar's, Selection Bar's and Filter Chips' strings
 * (KAN-364 part 2), and the DataGrid's, Wizard's, GlobalSearch's, Picker's,
 * InlineEditField's, DocumentPreview's and PresentMode's (KAN-422); none may
 * render now. */
export const FORMER_ENGLISH_DEFAULTS =
  /^(\+ New filter…|Save|Cancel|Views|Reset|Save current view as…|View name|Manage filters|Set as default|Remove as default|Delete view|Edit filter|Duplicate filter|Delete filter|Primary|No longer valid|Does not work|Delete|Consent|Scope|Confirm|None recorded|Label|Close|.* row actions|.* matching|contains|is exactly|is not|is|equals|greater than|less than|at least|at most|is today|is this week|is this month|is this year|is in the last N days|is more than N days ago|.* (contains|is exactly|is not|is|equals|greater than|less than|at least|at most) .*|.* is (today|this week|this month|this year)|.* is in the last \d+ days|.* is more than \d+ days ago|Not .*|Search…|Compact|Default|Comfortable|Density: .*|Change row density|Refresh|Clear|Clear selection|.* selected|Remove filter: .*|Showing .*|Loading…|Retry|Row actions|Select all|Select row|Next|Submit|Back|Recent|No matches|Search failed — try again\.|Not set|Download|Exit present mode)$/
