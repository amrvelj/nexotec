import { useState } from 'react'
import { Button } from '@mantine/core'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { EnvironmentNotice } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { SupportSessionBanner as Banner, SupportSessionRead } from '../../api/types'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { apiRefusalText } from '../../utils/apiRefusal'
import { formatTime } from '../../utils/format'
import { MY_SUPPORT_SESSION_KEY } from './useSupportSession'

/**
 * KAN-221 PR-6 (D-A-05, FR-A-18) — the persistent, non-dismissible banner of
 * a running support session, in the shell's one notice slot (ADR-084): who
 * from Nexotec is in the account, why, until when, and «Sitzung beenden».
 * The supported user ends it from here; so does the staff member acting for
 * them (PR 3). Once ended, the next read finds no session and it is gone.
 */
export function SupportSessionBanner({ session }: { session: Banner }) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const end = useMutation({
    // A second click is a 409 `support_session_ended`, never a second ending.
    mutationFn: () => api.post<SupportSessionRead>(`/platform/support-sessions/${session.id}/end`),
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: MY_SUPPORT_SESSION_KEY })
    },
    onError: (err) => {
      setError(apiRefusalText(t, err, 'supportSessions.banner.endError'))
      // A session already over (409) is simply gone on the next read.
      void queryClient.invalidateQueries({ queryKey: MY_SUPPORT_SESSION_KEY })
    },
  })
  const until = formatTime(session.expiresAt, locale)
  const text =
    session.viewer === 'staff'
      ? t('supportSessions.banner.staff', { user: session.targetUserName, until, reason: session.reason })
      : t('supportSessions.banner.user', { staff: session.staffName, until, reason: session.reason })
  return (
    <EnvironmentNotice
      action={
        <Button size="compact-xs" variant="white" color="dark" loading={end.isPending} onClick={() => end.mutate()}>
          {t('supportSessions.banner.end')}
        </Button>
      }
    >
      {text}
      {error && <> — {error}</>}
    </EnvironmentNotice>
  )
}
