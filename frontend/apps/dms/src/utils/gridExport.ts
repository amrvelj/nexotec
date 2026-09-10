// D-25 (PRD-Customers, ruled 2026-09-07) — Export and Print are the two
// bulk actions on an overview selection. Both take the SELECTED rows in the
// CURRENTLY VISIBLE columns: what the reader is looking at is what they
// get. This is the plain-browser primitive under those two actions; it is
// deliberately grid-agnostic (a column is just a header string plus a
// row -> text accessor) so Stock / Sales / Valuations can reuse it without
// a WP-6c change when their turn comes.

export interface ExportColumn<T> {
  header: string
  value: (row: T) => string
}

const CSV_NEEDS_QUOTING = /["\r\n,;]/

function csvCell(raw: string): string {
  return CSV_NEEDS_QUOTING.test(raw) ? `"${raw.replace(/"/g, '""')}"` : raw
}

/**
 * RFC 4180 with a UTF-8 BOM (U+FEFF) prepended so Excel on Windows reads
 * the accented Swiss data as UTF-8 rather than Latin-1. CRLF row
 * terminator, for the same reason.
 */
export function buildCsv<T>(columns: ExportColumn<T>[], rows: T[]): string {
  const lines = [
    columns.map((c) => csvCell(c.header)).join(','),
    ...rows.map((row) => columns.map((c) => csvCell(c.value(row))).join(',')),
  ]
  return `﻿${lines.join('\r\n')}\r\n`
}

export function downloadTextFile(filename: string, mimeType: string, content: string): void {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function exportRowsToCsv<T>(filename: string, columns: ExportColumn<T>[], rows: T[]): void {
  downloadTextFile(filename, 'text/csv;charset=utf-8', buildCsv(columns, rows))
}

const HTML_ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }

function htmlEscape(raw: string): string {
  return raw.replace(/[&<>"]/g, (ch) => HTML_ESCAPES[ch])
}

/**
 * Opens the selection as a bare printable table in a new window and calls
 * `print()` once it has loaded. Returns `false` when the browser blocked
 * the popup, so the caller can tell the user rather than failing silently.
 */
export function printRows<T>(title: string, columns: ExportColumn<T>[], rows: T[]): boolean {
  const win = window.open('', '_blank', 'noopener,noreferrer')
  if (!win) return false

  const headRow = `<tr>${columns.map((c) => `<th>${htmlEscape(c.header)}</th>`).join('')}</tr>`
  const bodyRows = rows
    .map((row) => `<tr>${columns.map((c) => `<td>${htmlEscape(c.value(row))}</td>`).join('')}</tr>`)
    .join('')

  // Named / system colours only — this string is scanned by the
  // no-hardcoded-colour architecture test, and a detached print document
  // cannot reach the app's token CSS variables anyway.
  win.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${htmlEscape(title)}</title>` +
      '<style>' +
      'body{font:12px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:24px}' +
      'h1{font-size:16px;margin:0 0 16px}' +
      'table{border-collapse:collapse;width:100%}' +
      'th,td{border:1px solid gray;padding:4px 8px;text-align:left;white-space:nowrap}' +
      'th{background:whitesmoke;font-weight:600}' +
      '</style></head><body>' +
      `<h1>${htmlEscape(title)}</h1>` +
      `<table><thead>${headRow}</thead><tbody>${bodyRows}</tbody></table>` +
      '<script>window.onload=function(){window.focus();window.print()}</script>' +
      '</body></html>',
  )
  win.document.close()
  return true
}
