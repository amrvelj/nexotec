import { useEffect, useState } from 'react'
import { Alert, Button, Card, Group, Select, Stack, Switch, Table, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type {
  NotificationPreferenceList,
  NotificationPreferenceRead,
  NotificationPreferenceUpdate,
  NotificationTiming,
  PushConfigRead,
} from '../../api/types'
import { NOTIFICATION_PREFERENCES_KEY, PUSH_CONFIG_KEY } from '../../hooks/useNotifications'
import {
  PushPermissionDenied,
  deviceSubscribed,
  disablePushOnThisDevice,
  enablePushOnThisDevice,
  pushSupported,
} from '../../utils/webPush'

const TIMINGS: NotificationTiming[] = ['immediate', 'digest', 'off']

/**
 * My account › Notifications (KAN-216; Dealer Administration D-A-40,
 * FR-A-48/49): the user decides everything, per family — immediately, a
 * daily digest or off, and whether it pushes. Never the manager. The
 * security family is locked: always immediate, in the inbox and as a push.
 * The inbox keeps every notification sent, so there is no in-app switch.
 * Below, push on this device: the browser's permission and subscription,
 * bound to this session.
 */
export function AccountNotificationsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('account.title'), t('account.tabs.notifications')])
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const preferences = useQuery({
    queryKey: NOTIFICATION_PREFERENCES_KEY,
    queryFn: () => api.get<NotificationPreferenceList>('/me/notification-preferences'),
  })
  const save = useMutation({
    mutationFn: ({ pref, body }: { pref: NotificationPreferenceRead; body: NotificationPreferenceUpdate }) =>
      api.put<NotificationPreferenceRead>(`/me/notification-preferences/${pref.family}`, body, {
        'If-Match': String(pref.version),
      }),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: NOTIFICATION_PREFERENCES_KEY })
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setError(t('notifications.preferences.conflict'))
        void queryClient.invalidateQueries({ queryKey: NOTIFICATION_PREFERENCES_KEY })
      } else setError(t('notifications.preferences.failed'))
    },
  })

  if (preferences.isError) return <Alert color="red" role="alert">{t('common.requestFailed')}</Alert>
  if (!preferences.data) return null

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Title order={4}>{t('notifications.preferences.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('notifications.preferences.intro')}
          </Text>
          <Table verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('notifications.preferences.family')}</Table.Th>
                <Table.Th>{t('notifications.preferences.timing')}</Table.Th>
                <Table.Th>{t('notifications.preferences.push')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {preferences.data.items.map((pref) => {
                const name = t(`notifications.families.${pref.family}.name`)
                const busy = save.isPending && save.variables?.pref.family === pref.family
                return (
                  <Table.Tr key={pref.family}>
                    <Table.Td>
                      <Text fw={600}>{name}</Text>
                      <Text fz="sm" c="dimmed">
                        {t(`notifications.families.${pref.family}.description`)}
                      </Text>
                      {!pref.switchable && (
                        <Group gap={4} mt={4} wrap="nowrap">
                          <Lock size={12} />
                          <Text fz="xs" c="dimmed">
                            {t('notifications.preferences.locked')}
                          </Text>
                        </Group>
                      )}
                      {pref.switchable && !pref.hasEvents && (
                        <Text fz="xs" c="dimmed" mt={4}>
                          {t('notifications.preferences.noEventsYet')}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Select
                        aria-label={`${t('notifications.preferences.timing')} – ${name}`}
                        data={TIMINGS.map((timing) => ({ value: timing, label: t(`notifications.timings.${timing}`) }))}
                        value={pref.timing}
                        disabled={!pref.switchable || busy}
                        allowDeselect={false}
                        onChange={(value) =>
                          value &&
                          save.mutate({ pref, body: { timing: value as NotificationTiming, inApp: true, push: pref.push } })
                        }
                        w={220}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Switch
                        aria-label={`${t('notifications.preferences.push')} – ${name}`}
                        checked={pref.push}
                        disabled={!pref.switchable || busy || pref.timing === 'off'}
                        onChange={(event) =>
                          save.mutate({ pref, body: { timing: pref.timing, inApp: true, push: event.currentTarget.checked } })
                        }
                      />
                    </Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>
        </Stack>
      </Card>
      <ThisDevicePush />
    </Stack>
  )
}

type DeviceState = 'checking' | 'unsupported' | 'unavailable' | 'denied' | 'on' | 'off'

function ThisDevicePush() {
  const { t } = useTranslation()
  const config = useQuery({ queryKey: PUSH_CONFIG_KEY, queryFn: () => api.get<PushConfigRead>('/me/push/config') })
  const [state, setState] = useState<DeviceState>('checking')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!config.data) return
    if (!config.data.available || !config.data.vapidPublicKey) {
      setState('unavailable')
      return
    }
    if (!pushSupported()) {
      setState('unsupported')
      return
    }
    if (Notification.permission === 'denied') {
      setState('denied')
      return
    }
    let live = true
    void deviceSubscribed().then((on) => live && setState(on ? 'on' : 'off'))
    return () => {
      live = false
    }
  }, [config.data])

  const toggle = useMutation({
    mutationFn: async (on: boolean) => {
      if (on) await enablePushOnThisDevice(config.data?.vapidPublicKey ?? '')
      else await disablePushOnThisDevice()
      return on
    },
    onSuccess: (on) => {
      setFailed(false)
      setState(on ? 'on' : 'off')
    },
    onError: (err) => {
      if (err instanceof PushPermissionDenied) setState('denied')
      else setFailed(true)
    },
  })

  if (config.isError) return <Alert color="red" role="alert">{t('common.requestFailed')}</Alert>

  const message: Record<DeviceState, string | null> = {
    checking: null,
    unavailable: t('notifications.device.unavailable'),
    unsupported: t('notifications.device.unsupported'),
    denied: t('notifications.device.denied'),
    on: t('notifications.device.active'),
    off: t('notifications.device.inactive'),
  }

  return (
    <Card withBorder radius="md" padding="lg">
      <Stack gap="sm" align="flex-start">
        <Title order={4}>{t('notifications.device.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('notifications.device.intro')}
        </Text>
        {message[state] && <Text fz="sm">{message[state]}</Text>}
        {failed && (
          <Alert color="red" role="alert">
            {t('notifications.device.failed')}
          </Alert>
        )}
        {(state === 'on' || state === 'off') && (
          <Button variant={state === 'on' ? 'default' : 'filled'} loading={toggle.isPending} onClick={() => toggle.mutate(state === 'off')}>
            {state === 'on' ? t('notifications.device.disable') : t('notifications.device.enable')}
          </Button>
        )}
      </Stack>
    </Card>
  )
}
