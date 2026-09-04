import { describe, expect, it } from 'vitest'
import {
  deriveReferenceView,
  referenceRowLanguageErrors,
  referenceRowMatchesQuery,
} from './referenceData'
import {
  DEFAULT_REFERENCE_LIST,
  REFERENCE_LIST_CODES,
  isReferenceListCode,
  referenceListLabel,
  referenceListOptions,
} from '../referenceLists'
import type { ReferenceListRead, ReferenceValueRead } from '../api/types'

function row(overrides: Partial<ReferenceValueRead> = {}): ReferenceValueRead {
  return {
    id: 'id',
    listCode: 'fuel_type',
    valueCode: 'petrol',
    labelDe: 'Benzin',
    labelFr: 'Essence',
    labelIt: 'Benzina',
    labelEn: 'Petrol',
    sortOrder: 0,
    active: true,
    version: 1,
    createdAt: '',
    updatedAt: '',
    createdBy: null,
    updatedBy: null,
    ...overrides,
  }
}

describe('referenceRowLanguageErrors', () => {
  it('is empty when all four labels are present', () => {
    expect(referenceRowLanguageErrors(row()).size).toBe(0)
  })

  it('flags an empty label as a row error (never a silent empty cell)', () => {
    const errs = referenceRowLanguageErrors(row({ labelIt: '' }))
    expect([...errs]).toEqual(['labelIt'])
  })

  it('treats whitespace-only as missing', () => {
    expect(referenceRowLanguageErrors(row({ labelFr: '   ' })).has('labelFr')).toBe(true)
  })

  it('flags every missing language, not just the first', () => {
    expect(referenceRowLanguageErrors(row({ labelDe: '', labelEn: '' }))).toEqual(new Set(['labelDe', 'labelEn']))
  })
})

describe('referenceRowMatchesQuery', () => {
  it('matches on the value code, case-insensitively', () => {
    expect(referenceRowMatchesQuery(row(), 'PET')).toBe(true)
  })

  it('matches on any of the four labels', () => {
    expect(referenceRowMatchesQuery(row(), 'benzina')).toBe(true)
    expect(referenceRowMatchesQuery(row(), 'diesel')).toBe(false)
  })

  it('an empty query matches everything', () => {
    expect(referenceRowMatchesQuery(row(), '  ')).toBe(true)
  })
})

describe('deriveReferenceView — a pasted URL reproduces the view (ADR-056)', () => {
  it('reads list and query straight back out of the search params', () => {
    const view = deriveReferenceView(new URLSearchParams('list=body_style&q=sedan'))
    expect(view).toEqual({ list: 'body_style', query: 'sedan' })
  })

  it('falls back to the default list for an unknown or absent code (never 404s the screen)', () => {
    expect(deriveReferenceView(new URLSearchParams('list=not_a_real_list')).list).toBe(DEFAULT_REFERENCE_LIST)
    expect(deriveReferenceView(new URLSearchParams()).list).toBe(DEFAULT_REFERENCE_LIST)
  })

  it('a bare screen has an empty query', () => {
    expect(deriveReferenceView(new URLSearchParams()).query).toBe('')
  })
})

function list(overrides: Partial<ReferenceListRead> = {}): ReferenceListRead {
  return {
    listCode: 'fuel_type',
    labelDe: 'Treibstoff',
    labelFr: 'Carburant',
    labelIt: 'Carburante',
    labelEn: 'Fuel type',
    valueCount: 6,
    activeValueCount: 6,
    createdAt: '',
    updatedAt: '',
    ...overrides,
  }
}

describe('referenceListLabel', () => {
  it('picks the label for the active UI language', () => {
    expect(referenceListLabel(list(), 'de')).toBe('Treibstoff')
    expect(referenceListLabel(list(), 'fr')).toBe('Carburant')
    expect(referenceListLabel(list(), 'it')).toBe('Carburante')
    expect(referenceListLabel(list(), 'en')).toBe('Fuel type')
  })

  it('falls back English → code so a picker entry is never blank', () => {
    expect(referenceListLabel(list({ labelDe: '' }), 'de')).toBe('Fuel type')
    expect(referenceListLabel(list({ labelDe: '', labelEn: '' }), 'de')).toBe('fuel_type')
  })
})

describe('referenceListOptions', () => {
  it('maps the fetched lists to value/label pairs in the server order', () => {
    const opts = referenceListOptions([list({ listCode: 'z_last' }), list({ listCode: 'a_first' })], 'de')
    expect(opts).toEqual([
      { value: 'z_last', label: 'Treibstoff' },
      { value: 'a_first', label: 'Treibstoff' },
    ])
  })

  it('falls back to the frozen set — code as its own label — until the fetch resolves', () => {
    const opts = referenceListOptions(undefined, 'de')
    expect(opts).toHaveLength(REFERENCE_LIST_CODES.length)
    expect(opts.every((o) => o.value === o.label)).toBe(true)
    expect(opts.map((o) => o.value)).toContain('fuel_type')
  })

  it('falls back when the server returns an empty set too', () => {
    expect(referenceListOptions([], 'fr')).toHaveLength(REFERENCE_LIST_CODES.length)
  })
})

describe('REFERENCE_LIST_CODES', () => {
  it('is a non-empty set of unique codes', () => {
    expect(REFERENCE_LIST_CODES.length).toBeGreaterThan(0)
    expect(new Set(REFERENCE_LIST_CODES).size).toBe(REFERENCE_LIST_CODES.length)
  })

  it('includes the default and the guard agrees', () => {
    expect(REFERENCE_LIST_CODES).toContain(DEFAULT_REFERENCE_LIST)
    expect(isReferenceListCode(DEFAULT_REFERENCE_LIST)).toBe(true)
    expect(isReferenceListCode('fuel_type')).toBe(true)
    expect(isReferenceListCode('nope')).toBe(false)
    expect(isReferenceListCode(null)).toBe(false)
  })
})
