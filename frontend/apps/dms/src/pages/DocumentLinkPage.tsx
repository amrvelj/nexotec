import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Group, Image, Loader, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { SUPPORTED_LANGUAGES } from '../i18n'
import { AuthScreen } from './auth/AuthScreen'

const TOKEN_KEY = 'nexotec.documentLink'

type LinkState =
  | { kind: 'loading' }
  | { kind: 'ready'; url: string; filename: string | null }
  | { kind: 'refused' }
  // The link named its sender, then the document could not be opened.
  | { kind: 'unavailable' }

type Sender = { name: string; logoUrl: string | null }

function readToken(): string {
  const fromHash = window.location.hash.replace(/^#/, '')
  try {
    if (fromHash) window.sessionStorage.setItem(TOKEN_KEY, fromHash)
    return fromHash || window.sessionStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return fromHash
  }
}

/** KAN-217 PR-4 (ADR-193): the page a contact opens from an SMS or a
 * WhatsApp message — `/d?lang=<language>#<token>`. No sign-in. The token is in
 * the URL's fragment, which a browser never sends to a server; the page takes
 * it out of the address bar at once and hands it to the API in a header. An
 * unknown, expired or used-up link is one message, as the server's one 404.
 *
 * `?lang=` is the contact's correspondence language (not a secret): the page
 * opens in it. The token is kept for this tab only (sessionStorage), so a
 * reload of the cleaned address still opens the document.
 *
 * KAN-217 PR-7 (ADR-208; Anto, 2026-11-20, option 1): the page is the
 * dealer's. It asks the link who sent it first; a link that cannot be read
 * names nobody and keeps the neutral text and frame. A link that named its
 * Betrieb and whose document then fails says «Wenden Sie sich an <Betrieb>». */
export function DocumentLinkPage() {
  const { t, i18n } = useTranslation()
  // Read once, before any effect clears it (an effect may run twice).
  const [token] = useState(readToken)
  const [state, setState] = useState<LinkState>(token ? { kind: 'loading' } : { kind: 'refused' })
  const [sender, setSender] = useState<Sender | null>(null)

  // Once per page, even when an effect runs twice.
  const languageApplied = useRef(false)
  useEffect(() => {
    if (languageApplied.current) return
    languageApplied.current = true
    const language = new URLSearchParams(window.location.search).get('lang')
    if (language && (SUPPORTED_LANGUAGES as readonly string[]).includes(language) && i18n.language !== language) {
      void i18n.changeLanguage(language)
    }
  }, [i18n])

  useEffect(() => {
    // The token leaves the address bar and the history entry at once.
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }
    if (!token) return
    const objectUrls: string[] = []
    let cancelled = false
    const keep = (url: string) => {
      objectUrls.push(url)
      if (cancelled) URL.revokeObjectURL(url)
      return !cancelled
    }
    void (async () => {
      let named: { name: string; hasLogo: boolean }
      try {
        named = await api.getDocumentLinkSender(token)
      } catch {
        if (!cancelled) setState({ kind: 'refused' })
        return
      }
      if (cancelled) return
      setSender({ name: named.name, logoUrl: null })
      if (named.hasLogo) {
        // A logo that does not load leaves the name alone.
        api
          .getDocumentLinkLogo(token)
          .then((logoUrl) => {
            if (keep(logoUrl)) setSender({ name: named.name, logoUrl })
          })
          .catch(() => undefined)
      }
      try {
        const { url, filename } = await api.getDocumentLink(token)
        if (keep(url)) setState({ kind: 'ready', url, filename })
      } catch {
        if (!cancelled) setState({ kind: 'unavailable' })
      }
    })()
    return () => {
      cancelled = true
      for (const url of objectUrls) URL.revokeObjectURL(url)
    }
  }, [token])

  return (
    <AuthScreen
      title={t('documentLink.title')}
      brand={
        sender && (
          <Stack gap="xs" align="center" data-testid="document-link-sender">
            {sender.logoUrl && <Image src={sender.logoUrl} alt="" h={48} w="auto" maw={240} fit="contain" />}
            <Text ta="center" fw={600} fz="lg">
              {sender.name}
            </Text>
          </Stack>
        )
      }
    >
      {state.kind === 'loading' && (
        <Group gap="sm">
          <Loader size="sm" />
          <Text fz="sm">{t('documentLink.loading')}</Text>
        </Group>
      )}
      {state.kind === 'refused' && (
        <Alert color="red" role="alert">
          {t('documentLink.refused')}
        </Alert>
      )}
      {state.kind === 'unavailable' && sender && (
        <Alert color="red" role="alert">
          {t('documentLink.unavailable', { name: sender.name })}
        </Alert>
      )}
      {state.kind === 'ready' && (
        <Stack gap="md">
          <Text fz="sm">{t('documentLink.ready')}</Text>
          <Group grow>
            <Button component="a" href={state.url} target="_blank" rel="noopener noreferrer">
              {t('documentLink.open')}
            </Button>
            <Button component="a" href={state.url} download={state.filename ?? true} variant="default">
              {t('documentLink.download')}
            </Button>
          </Group>
        </Stack>
      )}
    </AuthScreen>
  )
}
