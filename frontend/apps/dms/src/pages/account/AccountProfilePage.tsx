import { useEffect, useState } from 'react'
import { Alert, Button, Card, Group, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { MeRead, MeUpdate } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import { useUiPreferencesContext } from '../../hooks/UiPreferencesContext'
import { ME_KEY, phoneForApi } from './accountKeys'

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const
type Language = (typeof LANGUAGES)[number]
// The select's value for "the Betrieb's default" — the API's null.
const BETRIEB_DEFAULT = '__betrieb__'

interface ProfileForm {
  firstName: string
  lastName: string
  phone: string
  directDial: string
  correspondenceLanguage: string
}

function formOf(me: MeRead): ProfileForm {
  return {
    firstName: me.firstName,
    lastName: me.lastName,
    phone: me.phone ?? '',
    directDial: me.directDial ?? '',
    correspondenceLanguage: me.correspondenceLanguage ?? BETRIEB_DEFAULT,
  }
}

/** Only what changed, as PATCH semantics want; an emptied field is null. */
function changesOf(me: MeRead, form: ProfileForm): MeUpdate {
  const changes: MeUpdate = {}
  if (form.firstName.trim() !== me.firstName) changes.firstName = form.firstName.trim()
  if (form.lastName.trim() !== me.lastName) changes.lastName = form.lastName.trim()
  if (phoneForApi(form.phone) !== (me.phone ?? null)) changes.phone = phoneForApi(form.phone)
  if (phoneForApi(form.directDial) !== (me.directDial ?? null)) changes.directDial = phoneForApi(form.directDial)
  const language = form.correspondenceLanguage === BETRIEB_DEFAULT ? null : (form.correspondenceLanguage as Language)
  if (language !== (me.correspondenceLanguage ?? null)) changes.correspondenceLanguage = language
  return changes
}

/**
 * My account › Profile (KAN-201): the user's own name (Anto, 2026-10-20),
 * mobile, direct dial, the language of the documents they author, and the
 * UI language. The e-mail address is shown, never edited here: it is the
 * sign-in and recovery channel (KAN-196). Notification preferences arrive
 * with the notification catalogue (KAN-216).
 */
export function AccountProfilePage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('account.title'), t('account.tabs.profile')])
  const { refresh } = useAuth()
  const { uiLanguage, setUiLanguage } = useUiPreferencesContext()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ME_KEY, queryFn: () => api.get<MeRead>('/me') })
  // The record the form was loaded from: its changes and its If-Match come
  // from here, never from a later refetch — a change made elsewhere in the
  // meantime is a 409, not silently overwritten with this form's old values.
  const [loaded, setLoaded] = useState<MeRead | null>(null)
  const [form, setForm] = useState<ProfileForm | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!me.data) return
    // First load, or a newer version arrived while the form is untouched:
    // show it. An edited form keeps what it was loaded from.
    if (loaded === null || form === null || (me.data.version !== loaded.version && Object.keys(changesOf(loaded, form)).length === 0)) {
      setLoaded(me.data)
      setForm(formOf(me.data))
    }
  }, [me.data, loaded, form])

  const save = useMutation({
    mutationFn: ({ current, changes }: { current: MeRead; changes: MeUpdate }) =>
      api.patch<MeRead>('/me', changes, { 'If-Match': String(current.version) }),
    onSuccess: async (data) => {
      queryClient.setQueryData(ME_KEY, data)
      setLoaded(data)
      setForm(formOf(data))
      setError(null)
      setSaved(true)
      await refresh() // the sidebar card shows the name
    },
    onError: (err) => {
      setSaved(false)
      if (err instanceof ApiError && err.status === 409) {
        setError(t('account.profile.conflict'))
        void queryClient.invalidateQueries({ queryKey: ME_KEY })
      }
      else if (err instanceof ApiError && err.status === 422) setError(t('account.profile.invalid'))
      else setError(t('common.requestFailed'))
    },
  })

  if (me.isError) {
    return <Alert color="red" role="alert">{t('common.requestFailed')}</Alert>
  }
  if (!loaded || !form) return null
  const changes = changesOf(loaded, form)
  const dirty = Object.keys(changes).length > 0
  const set = (field: keyof ProfileForm, value: string) => {
    setForm({ ...form, [field]: value })
    setSaved(false)
  }
  const languageLabel = (code: string) => t(`account.languages.${code}`)

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {saved && !dirty && (
        <Alert color="green" role="status">
          {t('account.profile.saved')}
        </Alert>
      )}
      <Card withBorder radius="md" padding="lg">
        <form
          id="account-profile"
          onSubmit={(event) => {
            event.preventDefault()
            if (dirty && !save.isPending) save.mutate({ current: loaded, changes })
          }}
        >
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput
                label={t('account.profile.firstName')}
                required
                maxLength={100}
                value={form.firstName}
                onChange={(e) => set('firstName', e.currentTarget.value)}
              />
              <TextInput
                label={t('account.profile.lastName')}
                required
                maxLength={100}
                value={form.lastName}
                onChange={(e) => set('lastName', e.currentTarget.value)}
              />
              <TextInput
                label={t('account.profile.email')}
                description={t('account.profile.emailHint')}
                value={loaded.email}
                readOnly
                disabled
              />
              <div />
              <TextInput
                label={t('account.profile.phone')}
                placeholder="+41 79 123 45 67"
                value={form.phone}
                onChange={(e) => set('phone', e.currentTarget.value)}
              />
              <TextInput
                label={t('account.profile.directDial')}
                placeholder="+41 44 123 45 67"
                value={form.directDial}
                onChange={(e) => set('directDial', e.currentTarget.value)}
              />
              <Select
                label={t('account.profile.uiLanguage')}
                description={t('account.profile.uiLanguageHint')}
                data={LANGUAGES.map((code) => ({ value: code, label: languageLabel(code) }))}
                value={uiLanguage}
                allowDeselect={false}
                onChange={(value) => value && setUiLanguage(value as Language)}
              />
              <Select
                label={t('account.profile.correspondenceLanguage')}
                description={t('account.profile.correspondenceLanguageHint')}
                data={[
                  {
                    value: BETRIEB_DEFAULT,
                    label: t('account.profile.betriebDefault', {
                      language: languageLabel(loaded.defaultCorrespondenceLanguage),
                    }),
                  },
                  ...LANGUAGES.map((code) => ({ value: code, label: languageLabel(code) })),
                ]}
                value={form.correspondenceLanguage}
                allowDeselect={false}
                onChange={(value) => value && set('correspondenceLanguage', value)}
              />
            </SimpleGrid>
            <Group justify="flex-end">
              <Button
                variant="default"
                disabled={!dirty || save.isPending}
                onClick={() => {
                  // Back to the latest record: discarding the edit also takes in a newer version.
                  const latest = me.data ?? loaded
                  setLoaded(latest)
                  setForm(formOf(latest))
                  setError(null)
                }}
              >
                {t('common.cancel')}
              </Button>
              <Button type="submit" form="account-profile" disabled={!dirty} loading={save.isPending}>
                {t('common.save')}
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>
      <Card withBorder radius="md" padding="lg">
        <Group gap="sm" align="flex-start">
          <Bell size={20} />
          <div>
            <Text fw={600}>{t('account.profile.notificationsTitle')}</Text>
            <Text c="dimmed" fz="sm">
              {t('account.profile.notificationsSoon')}
            </Text>
          </div>
        </Group>
      </Card>
    </Stack>
  )
}
