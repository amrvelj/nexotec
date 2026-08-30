import { describe, expect, it } from 'vitest'
import { markDefault, removeView, renameView, upsertView, type SavedView } from './savedView'

const VIEWS: SavedView[] = [
  { id: 'a', name: 'My open leads', snapshot: {} },
  { id: 'b', name: 'Overdue', isDefault: true, snapshot: {} },
]

describe('upsertView', () => {
  it('appends a view with a new id', () => {
    const result = upsertView(VIEWS, { id: 'c', name: 'New view', snapshot: {} })
    expect(result.map((v) => v.id)).toEqual(['a', 'b', 'c'])
  })

  it('replaces a view with a matching id in place', () => {
    const result = upsertView(VIEWS, { id: 'a', name: 'Renamed', snapshot: {} })
    expect(result).toHaveLength(2)
    expect(result[0].name).toBe('Renamed')
  })
})

describe('removeView', () => {
  it('drops the matching view and leaves the rest untouched', () => {
    expect(removeView(VIEWS, 'a').map((v) => v.id)).toEqual(['b'])
  })
})

describe('renameView', () => {
  it('renames only the matching view', () => {
    const result = renameView(VIEWS, 'a', 'Renamed')
    expect(result.find((v) => v.id === 'a')?.name).toBe('Renamed')
    expect(result.find((v) => v.id === 'b')?.name).toBe('Overdue')
  })
})

describe('markDefault', () => {
  it('moves the default flag to the given id, clearing any previous one', () => {
    const result = markDefault(VIEWS, 'a')
    expect(result.find((v) => v.id === 'a')?.isDefault).toBe(true)
    expect(result.find((v) => v.id === 'b')?.isDefault).toBe(false)
  })

  it('clears the default entirely when given null', () => {
    const result = markDefault(VIEWS, null)
    expect(result.every((v) => !v.isDefault)).toBe(true)
  })
})
