import { describe, expect, it } from 'vitest'
import { deriveFilterFields } from './deriveFilters'
import { conditionsForField } from './filterPredicate'
import type { GridColumnDef } from './types'

// § ADR-058 — the filter field list is the column registry, not a second
// hand-maintained list. These prove the derivation and the per-field
// condition narrowing that lets a screen stop offering a predicate its API
// cannot honour (rather than accepting it and dropping it silently).

interface Row {
  type: string
  changedAt: string
  name: string
}

const columns: GridColumnDef<Row>[] = [
  { id: 'name', header: 'Name', cell: () => null },
  {
    id: 'type',
    header: 'Type',
    cell: () => null,
    meta: {
      filter: {
        type: 'select',
        param: 'customer_type',
        options: [{ value: 'a', label: 'A' }],
        conditions: ['is'],
      },
    },
  },
  {
    id: 'changedAt',
    header: 'Changed',
    cell: () => null,
    meta: { filter: { type: 'date', param: 'updated_since', conditions: ['today', 'inTheLastDays'] } },
  },
]

describe('deriveFilterFields', () => {
  it('emits one field per column that declares meta.filter, in registry order, and none for the rest', () => {
    const fields = deriveFilterFields(columns)
    expect(fields.map((f) => f.id)).toEqual(['type', 'changedAt'])
  })

  it('carries the column header as the label and maps id -> param', () => {
    const [typeField] = deriveFilterFields(columns)
    expect(typeField).toMatchObject({ id: 'type', label: 'Type', type: 'select', param: 'customer_type' })
  })

  it('defaults param to the column id when the column does not override it', () => {
    const [, changed] = deriveFilterFields(columns)
    expect(changed.param).toBe('updated_since')
    const noParam = deriveFilterFields([
      { id: 'lang', header: 'Language', cell: () => null, meta: { filter: { type: 'select' } } },
    ])
    expect(noParam[0].param).toBe('lang')
  })
})

describe('conditionsForField', () => {
  it('narrows the type’s full condition set to the declared subset', () => {
    const [typeField, changedField] = deriveFilterFields(columns)
    expect(conditionsForField(typeField).map((c) => c.value)).toEqual(['is'])
    expect(conditionsForField(changedField).map((c) => c.value)).toEqual(['today', 'inTheLastDays'])
    // "is not" (select) and "more than N days ago" (date) are deliberately
    // absent — the customer list endpoint cannot express either.
    expect(conditionsForField(changedField).map((c) => c.value)).not.toContain('moreThanDaysAgo')
  })

  it('offers every condition for the type when no subset is declared', () => {
    const [field] = deriveFilterFields([
      { id: 'x', header: 'X', cell: () => null, meta: { filter: { type: 'select' } } },
    ])
    expect(conditionsForField(field).map((c) => c.value)).toEqual(['is', 'isNot'])
  })

  it('falls back to the full set rather than an empty menu if the subset matches nothing', () => {
    const bogus = deriveFilterFields([
      { id: 'y', header: 'Y', cell: () => null, meta: { filter: { type: 'select', conditions: ['nope'] } } },
    ])[0]
    expect(conditionsForField(bogus).length).toBeGreaterThan(0)
  })
})
