import { describe, expect, it } from 'vitest'
import { buildCsv, type ExportColumn } from './gridExport'

// D-25 — Export writes the SELECTED rows in the CURRENTLY VISIBLE columns.
// buildCsv is the pure core; the component wiring (which rows, which
// columns) is covered by CustomersListPage.columns.render.test.tsx.

interface Row {
  name: string
  note: string
  count: number
}

const columns: ExportColumn<Row>[] = [
  { header: 'Name', value: (r) => r.name },
  { header: 'Note', value: (r) => r.note },
  { header: 'Count', value: (r) => String(r.count) },
]

describe('buildCsv', () => {
  it('emits a UTF-8 BOM, a header row, and one CRLF-terminated row per record', () => {
    const csv = buildCsv(columns, [
      { name: 'Muster AG', note: 'ok', count: 2 },
      { name: 'Bay', note: '', count: 0 },
    ])
    expect(csv.charCodeAt(0)).toBe(0xfeff)
    expect(csv.slice(1)).toBe('Name,Note,Count\r\nMuster AG,ok,2\r\nBay,,0\r\n')
  })

  it('quotes a value containing a comma, a quote, a semicolon or a newline, doubling embedded quotes', () => {
    const csv = buildCsv(columns, [
      { name: 'Meier, Hans', note: 'says "hi"', count: 1 },
      { name: 'line\nbreak', note: 'a;b', count: 3 },
    ])
    expect(csv).toContain('"Meier, Hans","says ""hi""",1')
    expect(csv).toContain('"line\nbreak","a;b",3')
  })

  it('renders an empty file body (header only) for an empty selection', () => {
    expect(buildCsv(columns, []).slice(1)).toBe('Name,Note,Count\r\n')
  })
})
