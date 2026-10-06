import { describe, expect, it } from 'vitest'
import { parseSortParam, serializeSort } from './sortParam'

describe('sortParam', () => {
  it('round-trips a multi-field sort', () => {
    const raw = 'stockNumber:asc,updatedAt:desc'
    expect(parseSortParam(raw)).toEqual([
      { field: 'stockNumber', direction: 'asc' },
      { field: 'updatedAt', direction: 'desc' },
    ])
    expect(serializeSort(parseSortParam(raw))).toBe(raw)
  })

  it('drops empty parts and treats anything but asc as desc', () => {
    expect(parseSortParam(',vin:sideways,')).toEqual([{ field: 'vin', direction: 'desc' }])
  })
})
