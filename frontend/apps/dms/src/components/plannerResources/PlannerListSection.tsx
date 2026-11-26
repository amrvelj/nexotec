import { Button, Group, Paper, Stack, Table, Text, Title, Tooltip } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge } from '@nexotec/ui-kit'
import type { TechnicianTypeRead } from '../../api/types'
import { formatNumber } from '../../utils/format'
import type { ListEntry, PlannerList } from './PlannerDialogs'
import { entryName } from './refusal'

/**
 * One of the planner's three lists (S7): workplace types, technician types
 * (with rank and counted-time factor) or qualifications (with «mandatory»).
 * A type copied from Nexotec's starting set is a sample until the manager
 * changes or confirms it (ADR-222); an entry a resource uses cannot be
 * deleted (the API's 409 `planning_list_entry_in_use`), so its delete is
 * offered disabled with the reason.
 */
export function PlannerListSection({
  list,
  entries,
  usage,
  canWrite,
  onAdd,
  onEdit,
  onConfirm,
  onDelete,
}: {
  list: PlannerList
  entries: ListEntry[]
  /** How many resources use each entry (active or not). */
  usage: (id: string) => number
  canWrite: boolean
  onAdd: () => void
  onEdit: (entry: ListEntry) => void
  onConfirm: (entry: ListEntry) => void
  onDelete: (entry: ListEntry) => void
}) {
  const { t, i18n } = useTranslation()
  const title = t(`plannerResources.${list}.title`)
  const technician = list === 'technicianTypes'
  const qualification = list === 'qualifications'
  const anySample = entries.some((entry) => 'isSample' in entry && entry.isSample)
  return (
    <Stack gap="xs" component="section" aria-label={title}>
      <Group justify="space-between">
        <div>
          <Title order={4}>{title}</Title>
          {anySample && (
            <Text c="dimmed" fz="sm">
              {t('plannerResources.samples.note')}
            </Text>
          )}
        </div>
        {canWrite && (
          <Button size="xs" onClick={onAdd}>
            {t(`plannerResources.${list}.add`)}
          </Button>
        )}
      </Group>
      {entries.length === 0 ? (
        <Text c="dimmed" fz="sm">
          {t(`plannerResources.${list}.empty`)}
        </Text>
      ) : (
        <Paper withBorder radius="md">
          <Table aria-label={title} verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('plannerResources.columns.name')}</Table.Th>
                {technician && <Table.Th>{t('plannerResources.columns.rank')}</Table.Th>}
                {technician && <Table.Th>{t('plannerResources.columns.factor')}</Table.Th>}
                {qualification && <Table.Th>{t('plannerResources.columns.mandatory')}</Table.Th>}
                <Table.Th>{t('plannerResources.columns.usedBy')}</Table.Th>
                <Table.Th aria-label={t('plannerResources.columns.actions')} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {entries.map((entry) => {
                const name = entryName(entry, i18n.language)
                const sample = 'isSample' in entry && entry.isSample
                const used = usage(entry.id)
                const technicianEntry = entry as TechnicianTypeRead
                return (
                  <Table.Tr key={entry.id}>
                    <Table.Td>
                      <Group gap="xs">
                        <Text fz="sm">{name}</Text>
                        {sample && <Badge tone="warning">{t('plannerResources.samples.badge')}</Badge>}
                      </Group>
                    </Table.Td>
                    {technician && (
                      <Table.Td>{technicianEntry.rank === null ? '–' : formatNumber(technicianEntry.rank)}</Table.Td>
                    )}
                    {technician && (
                      <Table.Td>
                        {technicianEntry.countedTimeFactor === null
                          ? t('plannerResources.technicianTypes.noFactor')
                          : formatNumber(Number(technicianEntry.countedTimeFactor), 2)}
                      </Table.Td>
                    )}
                    {qualification && (
                      <Table.Td>
                        {'isMandatory' in entry && entry.isMandatory ? (
                          <Badge tone="purple">{t('plannerResources.qualifications.mandatory')}</Badge>
                        ) : (
                          '–'
                        )}
                      </Table.Td>
                    )}
                    <Table.Td>{t('plannerResources.usedBy', { count: used })}</Table.Td>
                    <Table.Td>
                      {canWrite && (
                        <Group gap="xs" justify="flex-end" wrap="nowrap">
                          {sample && (
                            <Button
                              size="compact-xs"
                              variant="light"
                              aria-label={t('plannerResources.samples.confirmNamed', { name })}
                              onClick={() => onConfirm(entry)}
                            >
                              {t('plannerResources.samples.confirm')}
                            </Button>
                          )}
                          <Button
                            size="compact-xs"
                            variant="default"
                            aria-label={t('plannerResources.actions.editNamed', { name })}
                            onClick={() => onEdit(entry)}
                          >
                            {t('plannerResources.actions.edit')}
                          </Button>
                          <Tooltip label={t('plannerResources.actions.deleteInUse')} disabled={used === 0} withArrow>
                            <span>
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                color="red"
                                disabled={used > 0}
                                aria-label={t('plannerResources.actions.deleteNamed', { name })}
                                onClick={() => onDelete(entry)}
                              >
                                {t('plannerResources.actions.delete')}
                              </Button>
                            </span>
                          </Tooltip>
                        </Group>
                      )}
                    </Table.Td>
                  </Table.Tr>
                )
              })}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
    </Stack>
  )
}
