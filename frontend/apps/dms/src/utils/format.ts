export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('de-CH', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso))
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat('de-CH', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso)
  )
}
