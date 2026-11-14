import { useState } from 'react'
import { Alert, Anchor, Badge, Button, Group, List, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { OverviewCard } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { WebsiteListingRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'

/**
 * KAN-225 (D-A-26) — the car on the Betrieb's own website, the first
 * publishing channel. Not a marketplace: nothing is transmitted or deleted
 * at a third party, so taking it off asks no confirmation. What would refuse
 * it is listed before the action — the car's own data by the marketplace's
 * field name, the publishing profile's items in the dealer's words, with
 * a link to the Publishing screen.
 */
export function WebsiteListingCard({ stockItemId, canPublish }: { stockItemId: string; canPublish: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const key = ['stock-item', stockItemId, 'website-listing'] as const
  const path = `/inventory/stock-items/${stockItemId}/website-listing`
  const listing = useQuery({ queryKey: key, queryFn: () => api.get<WebsiteListingRead>(path) })
  const action = useMutation({
    mutationFn: (verb: 'publish' | 'unpublish') =>
      api.post<WebsiteListingRead>(`${path}/${verb}`, undefined, { 'If-Match': String(listing.data?.version) }),
    onSuccess: (data) => {
      setError(null)
      queryClient.setQueryData(key, data)
    },
    onError: (err) => setError(apiRefusalText(t, err, 'stockDetail.publishing.website.failed')),
  })
  const data = listing.data
  if (!data) return null
  const conditions = data.blockingConditions ?? []
  const profileItems = conditions.filter((c) => c.code)
  const carItems = conditions.filter((c) => !c.code)
  return (
    <OverviewCard title={t('stockDetail.publishing.website.title')}>
      <Stack gap="xs">
        <Group gap="xs">
          <Badge variant="light" color={data.isListed ? 'green' : 'gray'}>
            {data.isListed ? t('stockDetail.publishing.website.listed') : t('stockDetail.publishing.website.notListed')}
          </Badge>
        </Group>
        {!data.isListed && conditions.length > 0 && (
          <List size="sm" spacing={2}>
            {carItems.map((c) => (
              <List.Item key={`car-${c.field}`}>
                {c.field}: {c.message}
              </List.Item>
            ))}
            {profileItems.map((c) => (
              <List.Item key={`profile-${c.code}-${c.message}`}>
                {t(`publishingValidation.${c.code}`, { location: c.message })}
              </List.Item>
            ))}
          </List>
        )}
        {!data.isListed && profileItems.length > 0 && (
          <Anchor component={Link} to="/administration/publishing" fz="sm">
            {t('stockDetail.publishing.website.openProfile')}
          </Anchor>
        )}
        {canPublish && (
          <Group gap="xs">
            {data.isListed ? (
              <Button size="xs" variant="default" loading={action.isPending} onClick={() => action.mutate('unpublish')}>
                {t('stockDetail.publishing.website.unpublish')}
              </Button>
            ) : (
              <Button size="xs" disabled={conditions.length > 0} loading={action.isPending} onClick={() => action.mutate('publish')}>
                {t('stockDetail.publishing.website.publish')}
              </Button>
            )}
          </Group>
        )}
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Text fz="xs" c="dimmed">
          {t('stockDetail.publishing.website.hint')}
        </Text>
      </Stack>
    </OverviewCard>
  )
}
