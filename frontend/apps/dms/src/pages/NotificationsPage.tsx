import { Alert, Badge, Button, Card, Group, Loader, SegmentedControl, Stack, Text, Title, UnstyledButton } from '@mantine/core'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { NotificationPage, NotificationRead } from '../api/types'
import { NOTIFICATIONS_KEY, UNREAD_COUNT_KEY, notificationBody, notificationTitle } from '../hooks/useNotifications'
import { formatDateTime } from '../utils/format'

const PAGE_SIZE = 25

/**
 * The inbox (KAN-216; Dealer Administration FR-A-50): every notification,
 * newest first, read or unread, each with a link to its subject; kept 90
 * days. It is the record — a push only points here. The «unread» filter
 * lives in the URL (ADR-056). Opening an entry marks it read and goes to
 * its subject.
 */
export function NotificationsPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('notifications.title')])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const unreadOnly = params.get('filter') === 'unread'

  const inbox = useInfiniteQuery({
    queryKey: [...NOTIFICATIONS_KEY, { unreadOnly }],
    queryFn: ({ pageParam }) =>
      api.get<NotificationPage>(
        `/me/notifications?limit=${PAGE_SIZE}${unreadOnly ? '&unread=true' : ''}${
          pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''
        }`,
      ),
    initialPageParam: '' as string,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
      queryClient.invalidateQueries({ queryKey: UNREAD_COUNT_KEY }),
    ])

  const setRead = useMutation({
    mutationFn: ({ id, read }: { id: string; read: boolean }) =>
      api.patch<NotificationRead>(`/me/notifications/${id}`, { read }),
    onSuccess: refresh,
  })
  const markAll = useMutation({
    mutationFn: () => api.post<{ marked: number }>('/me/notifications/read-all'),
    onSuccess: refresh,
  })

  const open = (notification: NotificationRead) => {
    if (!notification.readAt) setRead.mutate({ id: notification.id, read: true })
    navigate(notification.link)
  }

  const items = inbox.data?.pages.flatMap((page) => page.items) ?? []
  const locale = `${i18n.language}-CH`

  return (
    <Stack gap="lg" p="md" maw={880}>
      <Group justify="space-between" align="center">
        <Title order={2}>{t('notifications.title')}</Title>
        <Button variant="default" onClick={() => markAll.mutate()} loading={markAll.isPending}>
          {t('notifications.markAllRead')}
        </Button>
      </Group>
      <SegmentedControl
        w="fit-content"
        value={unreadOnly ? 'unread' : 'all'}
        onChange={(value) => setParams(value === 'unread' ? { filter: 'unread' } : {})}
        data={[
          { value: 'all', label: t('notifications.filterAll') },
          { value: 'unread', label: t('notifications.filterUnread') },
        ]}
      />
      {(setRead.isError || markAll.isError) && (
        <Alert color="red" role="alert">
          {t('common.requestFailed')}
        </Alert>
      )}
      {inbox.isError ? (
        <Alert color="red" role="alert">
          {t('common.requestFailed')}
        </Alert>
      ) : inbox.isPending ? (
        <Loader size="sm" />
      ) : items.length === 0 ? (
        <Card withBorder radius="md" padding="lg">
          <Stack gap="xs" align="flex-start">
            <Bell size={20} />
            <Text fw={600}>{unreadOnly ? t('notifications.emptyUnread') : t('notifications.emptyTitle')}</Text>
            {!unreadOnly && (
              <Text c="dimmed" fz="sm">
                {t('notifications.emptyBody')}
              </Text>
            )}
          </Stack>
        </Card>
      ) : (
        <Stack gap="xs" component="ul" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {items.map((notification) => {
            const title = notificationTitle(t, notification)
            const unread = !notification.readAt
            return (
              <Card
                key={notification.id}
                component="li"
                withBorder
                radius="md"
                padding="md"
                aria-label={title}
                style={unread ? { borderLeft: '3px solid var(--purple-6)' } : undefined}
              >
                <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
                  <UnstyledButton onClick={() => open(notification)} style={{ flex: 1 }}>
                    <Group gap="xs" mb={4}>
                      <Text fw={unread ? 700 : 500}>{title}</Text>
                      {unread && (
                        <Badge size="xs" variant="light">
                          {t('notifications.unread')}
                        </Badge>
                      )}
                    </Group>
                    <Text fz="sm" c="dimmed">
                      {notificationBody(t, notification)}
                    </Text>
                    <Text fz="xs" c="dimmed" mt={4}>
                      {formatDateTime(notification.deliveredAt, locale)}
                    </Text>
                  </UnstyledButton>
                  <Group gap="xs" wrap="nowrap">
                    <Button size="xs" variant="subtle" onClick={() => open(notification)}>
                      {t('notifications.open')}
                    </Button>
                    <Button
                      size="xs"
                      variant="subtle"
                      onClick={() => setRead.mutate({ id: notification.id, read: unread })}
                    >
                      {unread ? t('notifications.markRead') : t('notifications.markUnread')}
                    </Button>
                  </Group>
                </Group>
              </Card>
            )
          })}
        </Stack>
      )}
      {inbox.hasNextPage && (
        <Button variant="default" w="fit-content" onClick={() => inbox.fetchNextPage()} loading={inbox.isFetchingNextPage}>
          {t('notifications.loadMore')}
        </Button>
      )}
      <Text fz="xs" c="dimmed">
        {t('notifications.retention')}
      </Text>
    </Stack>
  )
}
