/**
 * Two predicates on one number or date field are both applied (KAN-458):
 * each bound a grid's mapper sends is ANDed with one already set — the
 * higher lower bound, the lower upper bound — never overwritten, so the
 * request answers every chip on screen. Values compare as numbers, or as
 * ISO dates (`YYYY-MM-DD`, which order as strings).
 */
export function setLowerBound(params: URLSearchParams, name: string, value: number | string): void {
  const current = params.get(name)
  if (current === null || compare(value, current) > 0) params.set(name, String(value))
}

export function setUpperBound(params: URLSearchParams, name: string, value: number | string): void {
  const current = params.get(name)
  if (current === null || compare(value, current) < 0) params.set(name, String(value))
}

function compare(value: number | string, current: string): number {
  if (typeof value === 'number') return value - Number(current)
  return value < current ? -1 : value > current ? 1 : 0
}
