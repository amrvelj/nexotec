import { api } from '../../api/client'

/**
 * KAN-218 — an export's ZIP through the authenticated GET (no signed URL
 * before the bucket, KAN-402): fetched as a blob and handed to the browser
 * as a download under the export's own name.
 */
export async function downloadExport(path: string, exportId: string): Promise<void> {
  const url = await api.getBlobUrl(path)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = `nexotec-export-${exportId}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // The click has handed the file to the browser; the URL is not needed after it.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}
