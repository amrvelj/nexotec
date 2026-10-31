// KAN-216 (Dealer Administration FR-A-49/50) — the Web Push service worker.
// A push is a pointer to the inbox: it carries the title and an SPA link,
// never the notification's content. The inbox is the record. The title
// arrives already in the user's UI language (rendered by the backend), so
// this worker holds no user-visible string of its own.

const FALLBACK_LINK = '/notifications'

function safeLink(link) {
  // An SPA path only — never another origin.
  return typeof link === 'string' && link.startsWith('/') && !link.startsWith('//') ? link : FALLBACK_LINK
}

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  if (typeof data.title !== 'string' || data.title === '') return
  const link = safeLink(data.link)
  event.waitUntil(self.registration.showNotification(data.title, { data: { link }, icon: '/favicon.svg', tag: link }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const link = safeLink(event.notification.data && event.notification.data.link)
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if ('focus' in client) {
          if ('navigate' in client) client.navigate(link)
          return client.focus()
        }
      }
      return self.clients.openWindow(link)
    }),
  )
})
