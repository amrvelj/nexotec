import { useState } from 'react'
import { Button, Group, Paper, Select, Stack, Table, Text, Title } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge } from '@nexotec/ui-kit'
import type { LocationRead, QualificationRead, ResourceRead, TechnicianTypeRead, WorkplaceTypeRead } from '../../api/types'
import { entryName } from './refusal'

const ALL = '__all__'

/**
 * The planner's resources (S7): people and workplaces, grouped by location,
 * with a location filter. A person whose user no longer works in the Betrieb
 * (left, suspended or no longer a member: `personActsHere: false`) is marked
 * so the manager deactivates or replaces it; an inactive resource stays
 * listed, out of the planner, until reactivated.
 */
export function ResourcesSection({
  resources,
  locations,
  workplaceTypes,
  technicianTypes,
  qualifications,
  canWrite,
  onAddPerson,
  onAddWorkplace,
  onEdit,
  onWorkingHours,
  onDeactivate,
  onReactivate,
}: {
  resources: ResourceRead[]
  locations: LocationRead[]
  workplaceTypes: WorkplaceTypeRead[]
  technicianTypes: TechnicianTypeRead[]
  qualifications: QualificationRead[]
  canWrite: boolean
  onAddPerson: () => void
  onAddWorkplace: () => void
  onEdit: (resource: ResourceRead) => void
  onWorkingHours: (resource: ResourceRead) => void
  onDeactivate: (resource: ResourceRead) => void
  onReactivate: (resource: ResourceRead) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [filter, setFilter] = useState<string>(ALL)
  const title = t('plannerResources.resources.title')

  // Every active location, and an inactive one while a resource still stands there.
  const groups = [
    ...locations
      .filter((location) => location.isActive || resources.some((resource) => resource.locationId === location.id))
      .map((location) => ({ id: location.id, name: location.name, isActive: location.isActive })),
    // A resource whose location is not in the Betrieb's list (should not happen): under its own label.
    ...resources
      .filter((resource) => !locations.some((location) => location.id === resource.locationId))
      .filter((resource, index, all) => all.findIndex((r) => r.locationId === resource.locationId) === index)
      .map((resource) => ({ id: resource.locationId, name: resource.locationLabel, isActive: false })),
  ].filter((group) => filter === ALL || group.id === filter)

  const typeName = (resource: ResourceRead) => {
    const id = resource.kind === 'person' ? resource.technicianTypeId : resource.workplaceTypeId
    const list: (TechnicianTypeRead | WorkplaceTypeRead)[] = resource.kind === 'person' ? technicianTypes : workplaceTypes
    const entry = list.find((type) => type.id === id)
    return entry ? entryName(entry, language) : '–'
  }
  const qualificationNames = (resource: ResourceRead) =>
    resource.qualificationIds
      .map((id) => qualifications.find((entry) => entry.id === id))
      .filter((entry): entry is QualificationRead => entry !== undefined)
      .map((entry) => entryName(entry, language))
  const resourceName = (resource: ResourceRead) =>
    (resource.kind === 'person' ? resource.userLabel : resource.name) ?? '–'

  return (
    <Stack gap="sm" component="section" aria-label={title}>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={4}>{title}</Title>
          <Text c="dimmed" fz="sm">
            {t('plannerResources.resources.subtitle')}
          </Text>
        </div>
        <Group gap="xs" align="flex-end">
          <Select
            size="xs"
            w={220}
            label={t('plannerResources.resources.filter')}
            data={[
              { value: ALL, label: t('plannerResources.resources.allLocations') },
              ...locations.map((location) => ({ value: location.id, label: location.name })),
            ]}
            value={filter}
            allowDeselect={false}
            onChange={(value) => setFilter(value ?? ALL)}
          />
          {canWrite && (
            <>
              <Button size="xs" onClick={onAddPerson}>
                {t('plannerResources.resources.addPerson')}
              </Button>
              <Button size="xs" variant="default" onClick={onAddWorkplace}>
                {t('plannerResources.resources.addWorkplace')}
              </Button>
            </>
          )}
        </Group>
      </Group>
      {resources.length === 0 && (
        <Text c="dimmed" fz="sm">
          {t('plannerResources.resources.empty')}
        </Text>
      )}
      {resources.length > 0 &&
        groups.map((group) => {
          const here = resources.filter((resource) => resource.locationId === group.id)
          return (
            <Paper key={group.id} withBorder radius="md" p="sm" component="article" aria-label={group.name}>
              <Group gap="xs" mb="xs">
                <Text fw={600}>{group.name}</Text>
                {!group.isActive && <Badge tone="slate">{t('plannerResources.resources.locationInactive')}</Badge>}
              </Group>
              {here.length === 0 ? (
                <Text c="dimmed" fz="sm">
                  {t('plannerResources.resources.emptyAtLocation')}
                </Text>
              ) : (
                <Table aria-label={t('plannerResources.resources.tableLabel', { location: group.name })} verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t('plannerResources.columns.name')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.kind')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.type')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.qualifications')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.workingHours')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.sortOrder')}</Table.Th>
                      <Table.Th>{t('plannerResources.columns.status')}</Table.Th>
                      <Table.Th aria-label={t('plannerResources.columns.actions')} />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {here.map((resource) => {
                      const name = resourceName(resource)
                      const person = resource.kind === 'person'
                      return (
                        <Table.Tr key={resource.id}>
                          <Table.Td>
                            <Text fz="sm" td={resource.isActive ? undefined : 'line-through'}>
                              {name}
                            </Text>
                          </Table.Td>
                          <Table.Td>{t(`plannerResources.kinds.${resource.kind}`)}</Table.Td>
                          <Table.Td>{typeName(resource)}</Table.Td>
                          <Table.Td>
                            {person ? (
                              <Group gap={4}>
                                {qualificationNames(resource).map((qualification) => (
                                  <Badge key={qualification} tone="informational">
                                    {qualification}
                                  </Badge>
                                ))}
                                {resource.qualificationIds.length === 0 && '–'}
                              </Group>
                            ) : (
                              '–'
                            )}
                          </Table.Td>
                          <Table.Td>
                            {person ? (
                              <Button
                                size="compact-xs"
                                variant="subtle"
                                aria-label={t('plannerResources.hours.openNamed', { name })}
                                onClick={() => onWorkingHours(resource)}
                              >
                                {resource.workingHours.length === 0
                                  ? t('plannerResources.hours.notEntered')
                                  : t('plannerResources.hours.days', { count: resource.workingHours.length })}
                              </Button>
                            ) : (
                              '–'
                            )}
                          </Table.Td>
                          <Table.Td>{resource.sortOrder}</Table.Td>
                          <Table.Td>
                            <Group gap={4}>
                              <Badge tone={resource.isActive ? 'success' : 'slate'} dot>
                                {t(resource.isActive ? 'plannerResources.status.active' : 'plannerResources.status.inactive')}
                              </Badge>
                              {person && resource.personActsHere === false && (
                                <Badge tone="warning">{t('plannerResources.status.noLongerWorksHere')}</Badge>
                              )}
                            </Group>
                          </Table.Td>
                          <Table.Td>
                            {canWrite && (
                              <Group gap="xs" justify="flex-end" wrap="nowrap">
                                <Button
                                  size="compact-xs"
                                  variant="default"
                                  aria-label={t('plannerResources.actions.editNamed', { name })}
                                  onClick={() => onEdit(resource)}
                                >
                                  {t('plannerResources.actions.edit')}
                                </Button>
                                {resource.isActive ? (
                                  <Button
                                    size="compact-xs"
                                    variant="subtle"
                                    color="red"
                                    aria-label={t('plannerResources.actions.deactivateNamed', { name })}
                                    onClick={() => onDeactivate(resource)}
                                  >
                                    {t('plannerResources.actions.deactivate')}
                                  </Button>
                                ) : (
                                  <Button
                                    size="compact-xs"
                                    variant="subtle"
                                    aria-label={t('plannerResources.actions.reactivateNamed', { name })}
                                    onClick={() => onReactivate(resource)}
                                  >
                                    {t('plannerResources.actions.reactivate')}
                                  </Button>
                                )}
                              </Group>
                            )}
                          </Table.Td>
                        </Table.Tr>
                      )
                    })}
                  </Table.Tbody>
                </Table>
              )}
            </Paper>
          )
        })}
    </Stack>
  )
}
