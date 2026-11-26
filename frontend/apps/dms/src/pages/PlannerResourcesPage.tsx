import { useState } from 'react'
import { Alert, Skeleton, Stack, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type {
  LocationList,
  PersonCandidateList,
  QualificationList,
  ResourceList,
  ResourceRead,
  TechnicianTypeList,
  WorkplaceTypeList,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import {
  ConfirmDialog,
  ListEntryDialog,
  ResourceDialog,
  WorkingHoursDialog,
  type ListEntry,
  type PlannerList,
} from '../components/plannerResources/PlannerDialogs'
import { PlannerListSection } from '../components/plannerResources/PlannerListSection'
import { ResourcesSection } from '../components/plannerResources/ResourcesSection'
import { RefusalAlert } from '../components/plannerResources/RefusalAlert'
import { entryName, useRefusal } from '../components/plannerResources/refusal'
import { SettingRows } from '../components/settings/SettingRows'
import { useSettingArea } from '../components/settings/useSettings'
import { apiRefusalText } from '../utils/apiRefusal'

const GRID_MINUTES = 'planning.grid_minutes'
const SLOT_MINUTES = 'planning.slot_minutes'

const plannerKey = (dealershipId: string) => ['planner-resources', dealershipId]

const LIST_PATHS: Record<PlannerList, string> = {
  workplaceTypes: 'workplace-types',
  technicianTypes: 'technician-types',
  qualifications: 'qualifications',
}

/**
 * Dealer Administration › Planner resources (KAN-808, S7 of Aftersales slice 1;
 * A-RULE-0: the planner's master data is configured here, nowhere else). The
 * planner's two settings — O6, the grid, and O7, the length of a drop-off or
 * pick-up slot — shown by the shared `SettingRows`, the same rows as the
 * generic `/administration/settings/planning` (KAN-805 criterion 6); then the
 * three lists (workplace types, technician types, qualifications), and the
 * resources: people and workplaces per location, with their type,
 * qualifications and a person's working week.
 *
 * Aftersales and the manager read; the manager writes (`planner_resources`).
 * Every write to a versioned row sends If-Match; a stale version offers a
 * reload. No booking here: the planner itself is KAN-809.
 */
export function PlannerResourcesPage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('plannerResources.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('planner_resources')
  const queryClient = useQueryClient()
  const base = `/dealerships/${dealershipId}`
  const planning = `${base}/planning`
  const enabled = dealershipId !== ''

  const workplaceTypes = useQuery({
    queryKey: [...plannerKey(dealershipId), 'workplace-types'],
    queryFn: () => api.get<WorkplaceTypeList>(`${planning}/workplace-types`),
    enabled,
  })
  const technicianTypes = useQuery({
    queryKey: [...plannerKey(dealershipId), 'technician-types'],
    queryFn: () => api.get<TechnicianTypeList>(`${planning}/technician-types`),
    enabled,
  })
  const qualifications = useQuery({
    queryKey: [...plannerKey(dealershipId), 'qualifications'],
    queryFn: () => api.get<QualificationList>(`${planning}/qualifications`),
    enabled,
  })
  // Inactive ones too: they stay listed (reactivate) and count as using a list entry.
  const resources = useQuery({
    queryKey: [...plannerKey(dealershipId), 'resources'],
    queryFn: () => api.get<ResourceList>(`${planning}/resources?includeInactive=true`),
    enabled,
  })
  // Read under the write row: only the manager, who adds people, asks.
  const candidates = useQuery({
    queryKey: [...plannerKey(dealershipId), 'person-candidates'],
    queryFn: () => api.get<PersonCandidateList>(`${planning}/person-candidates`),
    enabled: enabled && canWrite,
  })
  const locations = useQuery({
    queryKey: ['locations', dealershipId],
    queryFn: () => api.get<LocationList>(`${base}/locations`),
    enabled,
  })
  // The settings area's shared query (KAN-805): SettingRows below writes and refreshes it.
  const settings = useSettingArea(dealershipId, 'planning')

  const { refusal, refuse, clear, reloadLabel } = useRefusal()
  const [entryDialog, setEntryDialog] = useState<{ list: PlannerList; entry: ListEntry | null } | null>(null)
  const [deleting, setDeleting] = useState<{ list: PlannerList; entry: ListEntry } | null>(null)
  const [resourceDialog, setResourceDialog] = useState<{ kind: 'person' | 'workplace'; resource: ResourceRead | null } | null>(
    null,
  )
  const [hoursFor, setHoursFor] = useState<ResourceRead | null>(null)
  const [deactivating, setDeactivating] = useState<ResourceRead | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: plannerKey(dealershipId) })
  // A stale version: every dialog closes and the screen reads what is there now.
  const reload = () => {
    clear()
    setEntryDialog(null)
    setDeleting(null)
    setResourceDialog(null)
    setHoursFor(null)
    setDeactivating(null)
    void refresh()
    void locations.refetch()
  }
  const run = async (call: () => Promise<unknown>) => {
    clear()
    try {
      await call()
      await refresh()
    } catch (err) {
      refuse(err)
    }
  }

  const resourceRows = resources.data?.items ?? []
  const usage = (list: PlannerList) => (id: string) =>
    resourceRows.filter((resource) =>
      list === 'workplaceTypes'
        ? resource.workplaceTypeId === id
        : list === 'technicianTypes'
          ? resource.technicianTypeId === id
          : resource.qualificationIds.includes(id),
    ).length
  const lists: Record<PlannerList, ListEntry[] | undefined> = {
    workplaceTypes: workplaceTypes.data?.items,
    technicianTypes: technicianTypes.data?.items,
    qualifications: qualifications.data?.items,
  }
  const loadError = [workplaceTypes, technicianTypes, qualifications, resources, settings].find((query) => query.isError)
  const loading = [workplaceTypes, technicianTypes, qualifications, resources, settings].some((query) => query.isLoading)
  const activeLocations = (locations.data?.items ?? []).filter((location) => location.isActive)
  const name = (entry: ListEntry) => entryName(entry, i18n.language)
  const resourceName = (resource: ResourceRead) => (resource.kind === 'person' ? resource.userLabel : resource.name) ?? ''

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('plannerResources.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('plannerResources.subtitle')}
        </Text>
      </div>
      {loadError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, loadError.error, 'plannerResources.loadError')}
        </Alert>
      )}
      <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={reload} onClose={clear} />
      {loading && <Skeleton height={240} radius="md" />}
      {!loading && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('plannerResources.readOnly')}
        </Text>
      )}

      {settings.data && (
        <Stack gap="xs" component="section" aria-label={t('plannerResources.settings.title')}>
          <Title order={4}>{t('plannerResources.settings.title')}</Title>
          {/* KAN-805 criterion 6: the same rows as the generic settings screen
              (/administration/settings/planning) — the same value, history and
              per-location column, the grid's and the slot's rules hinted (KAN-808). */}
          <SettingRows
            dealershipId={dealershipId}
            data={settings.data}
            keys={[GRID_MINUTES, SLOT_MINUTES]}
            onReload={() => void settings.refetch()}
          />
        </Stack>
      )}

      {(['workplaceTypes', 'technicianTypes', 'qualifications'] as const).map((list) => {
        const entries = lists[list]
        if (!entries) return null
        const path = `${planning}/${LIST_PATHS[list]}`
        return (
          <PlannerListSection
            key={list}
            list={list}
            entries={entries}
            usage={usage(list)}
            canWrite={canWrite}
            onAdd={() => setEntryDialog({ list, entry: null })}
            onEdit={(entry) => setEntryDialog({ list, entry })}
            onConfirm={(entry) =>
              void run(() => api.post(`${path}/${entry.id}/confirmation`, undefined, { 'If-Match': String(entry.version) }))
            }
            onDelete={(entry) => setDeleting({ list, entry })}
          />
        )
      })}

      {resources.data && (
        <ResourcesSection
          resources={resourceRows}
          locations={locations.data?.items ?? []}
          workplaceTypes={workplaceTypes.data?.items ?? []}
          technicianTypes={technicianTypes.data?.items ?? []}
          qualifications={qualifications.data?.items ?? []}
          canWrite={canWrite}
          onAddPerson={() => setResourceDialog({ kind: 'person', resource: null })}
          onAddWorkplace={() => setResourceDialog({ kind: 'workplace', resource: null })}
          onEdit={(resource) => setResourceDialog({ kind: resource.kind, resource })}
          onWorkingHours={setHoursFor}
          onDeactivate={setDeactivating}
          onReactivate={(resource) =>
            void run(() =>
              api.patch(`${planning}/resources/${resource.id}`, { isActive: true }, { 'If-Match': String(resource.version) }),
            )
          }
        />
      )}

      {entryDialog && (
        <ListEntryDialog
          list={entryDialog.list}
          entry={entryDialog.entry}
          onClose={() => setEntryDialog(null)}
          onReload={reload}
          onSave={async (body) => {
            const path = `${planning}/${LIST_PATHS[entryDialog.list]}`
            const entry = entryDialog.entry
            if (entry && body) {
              await api.patch(`${path}/${entry.id}`, body, { 'If-Match': String(entry.version) })
            } else if (!entry && body) {
              await api.post(path, body)
            }
            setEntryDialog(null)
            await refresh()
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={t(`plannerResources.${deleting.list}.deleteTitle`)}
          body={t('plannerResources.actions.deleteBody', { name: name(deleting.entry) })}
          actionLabel={t('plannerResources.actions.delete')}
          onClose={() => setDeleting(null)}
          onReload={reload}
          onConfirm={async () => {
            await api.delete(`${planning}/${LIST_PATHS[deleting.list]}/${deleting.entry.id}`, {
              'If-Match': String(deleting.entry.version),
            })
            setDeleting(null)
            await refresh()
          }}
        />
      )}
      {resourceDialog && (
        <ResourceDialog
          kind={resourceDialog.kind}
          resource={resourceDialog.resource}
          candidates={candidates.data?.items ?? []}
          locations={activeLocations}
          workplaceTypes={workplaceTypes.data?.items ?? []}
          technicianTypes={technicianTypes.data?.items ?? []}
          qualifications={qualifications.data?.items ?? []}
          onClose={() => setResourceDialog(null)}
          onReload={reload}
          onSave={async (body) => {
            const resource = resourceDialog.resource
            if (resource && body) {
              await api.patch(`${planning}/resources/${resource.id}`, body, { 'If-Match': String(resource.version) })
            } else if (!resource && body) {
              await api.post(`${planning}/resources`, { kind: resourceDialog.kind, ...body })
            }
            setResourceDialog(null)
            await refresh()
          }}
        />
      )}
      {hoursFor && (
        <WorkingHoursDialog
          resource={hoursFor}
          canWrite={canWrite}
          onClose={() => setHoursFor(null)}
          onReload={reload}
          onSave={async (body) => {
            await api.put(`${planning}/resources/${hoursFor.id}/working-hours`, body, {
              'If-Match': String(hoursFor.version),
            })
            setHoursFor(null)
            await refresh()
          }}
        />
      )}
      {deactivating && (
        <ConfirmDialog
          title={t('plannerResources.deactivate.title')}
          body={t('plannerResources.deactivate.body', { name: resourceName(deactivating) })}
          actionLabel={t('plannerResources.actions.deactivate')}
          onClose={() => setDeactivating(null)}
          onReload={reload}
          onConfirm={async () => {
            await api.patch(
              `${planning}/resources/${deactivating.id}`,
              { isActive: false },
              { 'If-Match': String(deactivating.version) },
            )
            setDeactivating(null)
            await refresh()
          }}
        />
      )}
    </Stack>
  )
}
