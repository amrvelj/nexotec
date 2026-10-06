import type { SortSpec } from '@nexotec/ui-kit'

// § ADR-056 — a grid's sort lives in the URL as `?sort=field:dir,field:dir`,
// the same spelling the API's `sort` parameter takes.

export function parseSortParam(raw: string): SortSpec[] {
  return raw
    .split(',')
    .map((part): SortSpec | null => {
      const [field, direction] = part.split(':')
      if (!field) return null
      return { field, direction: direction === 'asc' ? 'asc' : 'desc' }
    })
    .filter((s): s is SortSpec => s !== null)
}

export function serializeSort(sort: SortSpec[]): string {
  return sort.map((s) => `${s.field}:${s.direction}`).join(',')
}
