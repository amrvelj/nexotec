import { describe, expect, it } from 'vitest'
import {
  deriveReferenceView,
  referenceRowLanguageErrors,
  referenceRowMatchesQuery,
} from './referenceData'
import { DEFAULT_REFERENCE_LIST, REFERENCE_LIST_CODES, isReferenceListCode } from '../referenceLists'
import type { ReferenceValueRead } from '../api/types'

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
