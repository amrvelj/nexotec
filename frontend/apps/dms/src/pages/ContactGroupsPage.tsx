import { useState } from 'react'
import { Alert, Paper, Skeleton, Stack, Switch, Table, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { ContactGroupList, ContactGroupRead } from '../api/types'
import { useCanWrite } from '../auth/useCanWrite'
import { contactGroupsKey, useContactGroups } from '../hooks/useContactGroups'
import { apiRefusalText } from '../utils/apiRefusal'

/**
 * Dealer Administration › Kontaktgruppen (KAN-466; Anto, 2026-10-26).
 * The list — Privat, Gewerbe, Flotte, … — is Nexotec's (platform reference
 * data, edited by staff on Reference data); the Betrieb switches on the groups
 * it uses here, the one place it configures them (A-RULE-0). Any role reads;
 * the manager switches (matrix row «Dealership settings»). A group switched
 * off stays on the contacts that carry it; one Nexotec withdrew shows as such
 * and cannot be switched on.
 */
export function ContactGroupsPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('contactGroups.title')])
  const { dealershipId, query, items, label } = useContactGroups()
  const canWrite = useCanWrite('dealership_settings')
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const toggle = async (group: ContactGroupRead, enabled: boolean) => {
    setPending(group.valueCode)
    setError(null)
    try {
      const updated = await api.put<ContactGroupRead>(
        `/dealerships/${dealershipId}/contact-groups/${encodeURIComponent(group.valueCode)}`,
        { enabled },
      )
      queryClient.setQueryData<ContactGroupList>(contactGroupsKey(dealershipId), (list) =>
        list ? { items: list.items.map((item) => (item.valueCode === updated.valueCode ? updated : item)) } : list,
      )
    } catch (err) {
      setError(apiRefusalText(t, err, 'contactGroups.saveError'))
    } finally {
      setPending(null)
    }
  }

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('contactGroups.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('contactGroups.subtitle')}
        </Text>
      </div>
      <Alert color="blue" variant="light">
        {t('contactGroups.offNotice')}
      </Alert>
      {query.isError && (
        <Alert color="red" role="alert">
          {t('contactGroups.loadError')}
        </Alert>
      )}
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {query.isLoading && <Skeleton height={240} radius="md" />}
      {query.data && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('contactGroups.readOnly')}
        </Text>
      )}
      {query.data && (
        <Paper withBorder radius="md">
          <Table striped highlightOnHover aria-label={t('contactGroups.title')}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('contactGroups.columns.group')}</Table.Th>
                <Table.Th>{t('contactGroups.columns.status')}</Table.Th>
                <Table.Th>{t('contactGroups.columns.enabled')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((group) => (
                <Table.Tr key={group.valueCode}>
                  <Table.Td>{label(group.valueCode)}</Table.Td>
                  <Table.Td>
                    {!group.active ? (
                      <Badge tone="slate">{t('contactGroups.status.inactive')}</Badge>
                    ) : group.enabled ? (
                      <Badge tone="success" dot>
                        {t('contactGroups.status.on')}
                      </Badge>
                    ) : (
                      <Badge tone="slate" dot>
                        {t('contactGroups.status.off')}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Switch
                      checked={group.enabled}
                      disabled={!canWrite || pending !== null || (!group.active && !group.enabled)}
                      onChange={(event) => void toggle(group, event.currentTarget.checked)}
                      aria-label={t('contactGroups.switchLabel', { group: label(group.valueCode) })}
                    />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
    </Stack>
  )
}
