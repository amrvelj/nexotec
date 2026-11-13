import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { sortSpecFromQueryParam, sortSpecToQueryParam, type SortSpec } from '@nexotec/ui-kit'

/**
 * A grid's sort as ADR-056 wants it: in the URL (`?sort=title:asc`, or the
 * grid's own parameter where a screen holds two grids), the shareable unit;
 * the user's grid preference is the fallback when the URL carries none, and
 * every change is stored in both. Every other URL parameter is kept.
 */
export function useSortInUrl(
  gridPrefs: { sort: SortSpec[]; setSort: (sort: SortSpec[]) => void },
  param = 'sort',
): [SortSpec[], (next: SortSpec[]) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const raw = searchParams.get(param)
  const sort = raw ? sortSpecFromQueryParam(raw) : gridPrefs.sort
  const { setSort: storeSort } = gridPrefs
  const setSort = useCallback(
    (next: SortSpec[]) => {
      storeSort(next)
      setSearchParams(
        (prev) => {
          const updated = new URLSearchParams(prev)
          const value = sortSpecToQueryParam(next)
          if (value) updated.set(param, value)
          else updated.delete(param)
          return updated
        },
        { replace: true },
      )
    },
    [storeSort, setSearchParams, param],
  )
  return [sort, setSort]
}
