import { formatNumber } from '../../utils/format'

/** A file size the way the list says it: whole kilobytes up to a megabyte, then megabytes with one decimal. */
export function formatByteSize(bytes: number | null): string {
  if (bytes === null) return '—'
  if (bytes < 1024 * 1024) return `${formatNumber(Math.max(1, Math.round(bytes / 1024)))} KB`
  return `${formatNumber(Math.round((bytes / (1024 * 1024)) * 10) / 10)} MB`
}
