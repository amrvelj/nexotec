// FR-13: "Locale formatting: dates dd.MM.yyyy" — this is a fixed Swiss
// convention shared by all four UI languages, not something that changes
// per language the way translated strings do. The `locale` param still
// takes the active de-CH/fr-CH/it-CH/en-CH tag (not hardcoded 'de-CH') so
// this stays correct if a locale's date convention ever needs to diverge.
export function formatDate(iso: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso))
}

export function formatDateTime(iso: string, locale = 'de-CH'): string {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso)
  )
}

// WP-6c PR-12: the frontend mirror of app.core.i18n's format_number_ch/
// format_currency_chf — "identical across all four languages, takes no
// language parameter at all" (that module's own docstring), so unlike
// formatDate/formatDateTime above there is deliberately no `locale`
// parameter here. It matters more than it sounds: Intl.NumberFormat's own
// fr-CH data uses a COMMA decimal separator (verified — `1'234,5` under
// fr-CH vs `1'234.5` under de-CH/it-CH/en-CH), which would silently
// diverge from every PDF WeasyPrint renders (always a period) if this
// forwarded the active UI language instead of pinning to 'de-CH'. The
// grouping separator is pinned too (KAN-156): it is not taken from Intl's
// output, because CLDR's own de-CH data changed it — older ICU builds give
// the ASCII apostrophe (U+0027), newer ones (ICU 77 / CLDR 46+, and the
// browsers that ship them) the typographic U+2019. The PDFs always print
// U+0027, so formatSwiss rebuilds the string from formatToParts and writes
// every group and decimal part itself; only the digits come from Intl.

const GROUP = "'"
const DECIMAL = '.'

function formatSwiss(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat('de-CH', options)
    .formatToParts(value)
    .map((part) => (part.type === 'group' ? GROUP : part.type === 'decimal' ? DECIMAL : part.value))
    .join('')
}

export function formatNumber(value: number): string {
  return formatSwiss(value)
}

export function formatCurrencyChf(value: number): string {
  const rounded = Math.round(value * 100) / 100
  const sign = rounded < 0 ? '− ' : '' // real minus sign U+2212, matching format_currency_chf
  const formatted = formatSwiss(Math.abs(rounded), { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${sign}CHF ${formatted}`
}
