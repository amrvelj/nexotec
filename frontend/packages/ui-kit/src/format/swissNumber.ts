// The frontend mirror of app.core.i18n's format_number_ch/
// format_currency_chf — "identical across all four languages, takes no
// language parameter at all" (that module's own docstring), so there is
// deliberately no `locale` parameter here. It matters more than it sounds:
// Intl.NumberFormat's own fr-CH data uses a COMMA decimal separator
// (verified — `1'234,5` under fr-CH vs `1'234.5` under de-CH/it-CH/en-CH),
// which would silently diverge from every PDF WeasyPrint renders (always a
// period) if this forwarded the active UI language instead of pinning to
// 'de-CH'. The grouping separator is pinned too (KAN-156): it is not taken
// from Intl's output, because CLDR's own de-CH data changed it — older ICU
// builds give the ASCII apostrophe (U+0027), newer ones (ICU 77 / CLDR 46+,
// and the browsers that ship them) the typographic U+2019. The PDFs always
// print U+0027, so formatSwiss rebuilds the string from formatToParts and
// writes every group and decimal part itself; only the digits come from Intl.
//
// It lives in the kit, not in apps/dms (KAN-160), because the kit's own
// DataGrid footer total needs it and the kit cannot import from an app.
// apps/dms/src/utils/format.ts re-exports both functions; this file is the
// one implementation, and the only place allowed to call Intl.NumberFormat
// (frontend/apps/dms/src/architecture/no-locale-number-format.test.ts).

const GROUP = "'";
const DECIMAL = ".";

function formatSwiss(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat("de-CH", options)
    .formatToParts(value)
    .map((part) => (part.type === "group" ? GROUP : part.type === "decimal" ? DECIMAL : part.value))
    .join("");
}

// `fractionDigits` fixes the decimals shown (KAN-164). format_number_ch
// prints a Decimal at its own scale (`Decimal("1234.5000")` → `1'234.5000`);
// a JS number carries no scale, so a caller rendering a DECIMAL column states
// the column's scale here. Without it Intl rounds to at most 3 decimals,
// which would silently drop the fourth of a DECIMAL(12, 4).
export function formatNumber(value: number, fractionDigits?: number): string {
  if (fractionDigits === undefined) return formatSwiss(value);
  return formatSwiss(value, { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits });
}

export function formatCurrencyChf(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  const sign = rounded < 0 ? "− " : ""; // real minus sign U+2212, matching format_currency_chf
  const formatted = formatSwiss(Math.abs(rounded), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${sign}CHF ${formatted}`;
}
