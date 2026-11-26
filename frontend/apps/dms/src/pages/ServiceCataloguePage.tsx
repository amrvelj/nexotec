import { useState } from 'react'
import { Alert, Button, Group, Paper, Skeleton, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Badge, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type {
  AreaSettingsRead,
  LabourRateCategoryList,
  ServicePackageList,
  ServicePackageRead,
  ServicePackageWrite,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { RefusalAlert } from '../components/plannerResources/RefusalAlert'
import { entryName, useRefusal } from '../components/plannerResources/refusal'
import { ServicePackageDialog, type TimeUnitInForce } from '../components/serviceCatalogue/ServicePackageDialog'
import { useSettingArea } from '../components/settings/useSettings'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatNumber } from '../utils/format'

const TIME_UNIT = 'labour_rates.time_unit'
const WORK_UNITS_PER_HOUR = 'labour_rates.work_units_per_hour'

const catalogueKey = (dealershipId: string) => ['service-catalogue', dealershipId]

/** O18/O19 of the Betrieb itself (a package is Betrieb-wide; ruling 183). Not set: hours. */
function unitOf(settings: AreaSettingsRead | undefined): TimeUnitInForce {
  const slot = (key: string) => settings?.settings.find((setting) => setting.declaration.key === key)?.dealership
  const unit = slot(TIME_UNIT)
  const perHour = slot(WORK_UNITS_PER_HOUR)
  if (unit?.isSet && unit.value?.choice === 'work_units') {
    return { unit: 'work_units', workUnitsPerHour: perHour?.isSet ? (perHour.value?.number ?? null) : null }
  }
  return { unit: 'hours', workUnitsPerHour: null }
}

/**
 * Dealer Administration › Service catalogue (KAN-818, S1 of Aftersales slice 1;
 * section 3.10, ruling 182). A package is a named piece of work: labour
 * positions with a fixed time and a labour category, «Prüfung nötig» per
 * labour position, and text positions; its name typed once with an optional
 * name per language (ruling 176). The time is typed and shown in the
 * Betrieb's unit, hours or work units (ruling 183). The data is Aftersales';
 * the screen is here (A-RULE-0).
 *
 * The desk reads (it puts a package onto a job); the manager writes
 * (`service_packages`). A package is never deleted: set inactive, it is not
 * offered for a new job, and no order that holds its positions changes
 * (check 45). Nexotec ships no package (ruling 174).
 */
export function ServiceCataloguePage() {
  const { t, i18n } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('serviceCatalogue.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('service_packages')
  const queryClient = useQueryClient()
  const base = `/dealerships/${dealershipId}`
  const enabled = dealershipId !== ''

  const packages = useQuery({
    queryKey: [...catalogueKey(dealershipId), 'packages'],
    queryFn: () => api.get<ServicePackageList>(`${base}/aftersales/service-packages`),
    enabled,
  })
  const categories = useQuery({
    queryKey: [...catalogueKey(dealershipId), 'categories'],
    queryFn: () => api.get<LabourRateCategoryList>(`${base}/labour-rate-categories`),
    enabled,
  })
  const settings = useSettingArea(dealershipId, 'labour_rates')
  const unitInForce = unitOf(settings.data)

  const { refusal, refuse, clear, reloadLabel } = useRefusal('serviceCatalogue.saveError')
  const [dialog, setDialog] = useState<{ pkg: ServicePackageRead | null } | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: catalogueKey(dealershipId) })
  const reload = () => {
    clear()
    setDialog(null)
    void refresh()
  }
  const save = async (pkg: ServicePackageRead | null, body: ServicePackageWrite) => {
    if (pkg) {
      await api.put(`${base}/aftersales/service-packages/${pkg.id}`, body, { 'If-Match': String(pkg.version) })
    } else {
      await api.post(`${base}/aftersales/service-packages`, body)
    }
    setDialog(null)
    await refresh()
  }
  const toggleActive = async (pkg: ServicePackageRead) => {
    clear()
    try {
      await save(pkg, {
        name: pkg.name,
        nameTranslations: pkg.nameTranslations,
        isActive: !pkg.isActive,
        positions: pkg.positions.map((position) => ({
          id: position.id,
          kind: position.kind,
          text: position.text,
          labourCategoryId: position.labourCategoryId,
          time: position.time?.quantity ?? null,
          timeUnit: position.time?.unit ?? null,
          checkNeeded: position.checkNeeded,
        })),
      })
    } catch (err) {
      refuse(err)
    }
  }

  const loadError = [packages, categories].find((query) => query.isError)
  const loading = [packages, categories, settings].some((query) => query.isLoading)
  const rows = packages.data?.items ?? []
  const timeText = (pkg: ServicePackageRead) => {
    const labour = pkg.positions.filter((position) => position.time)
    if (labour.length === 0) return '–'
    const units = new Set(labour.map((position) => position.time?.unit))
    if (units.size > 1) return t('serviceCatalogue.mixedUnits')
    const total = labour.reduce((sum, position) => sum + Number(position.time?.quantity ?? 0), 0)
    const unit = labour[0].time?.unit ?? 'hours'
    return `${formatNumber(total, 2)} ${t(`serviceCatalogue.unitShort.${unit}`)}`
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{t('serviceCatalogue.title')}</Title>
          <Text c="dimmed" fz="sm">
            {t('serviceCatalogue.subtitle')}
          </Text>
        </div>
        {canWrite && (
          <Button onClick={() => setDialog({ pkg: null })} disabled={!categories.data}>
            {t('serviceCatalogue.add')}
          </Button>
        )}
      </Group>
      {loadError && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, loadError.error, 'serviceCatalogue.loadError')}
        </Alert>
      )}
      <RefusalAlert refusal={refusal} reloadLabel={reloadLabel} onReload={reload} onClose={clear} />
      {loading && <Skeleton height={240} radius="md" />}
      {!loading && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('serviceCatalogue.readOnly')}
        </Text>
      )}
      {!loading && settings.data && (
        <Text fz="sm" c="dimmed">
          {unitInForce.unit === 'work_units'
            ? t('serviceCatalogue.unitInForce.work_units', { perHour: formatNumber(Number(unitInForce.workUnitsPerHour ?? 0)) })
            : t('serviceCatalogue.unitInForce.hours')}
        </Text>
      )}
      {packages.data && rows.length === 0 && (
        <Text c="dimmed" fz="sm">
          {t('serviceCatalogue.empty')}
        </Text>
      )}
      {rows.length > 0 && (
        <Paper withBorder radius="md">
          <Table aria-label={t('serviceCatalogue.title')} verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('serviceCatalogue.columns.name')}</Table.Th>
                <Table.Th>{t('serviceCatalogue.columns.positions')}</Table.Th>
                <Table.Th>{t('serviceCatalogue.columns.time')}</Table.Th>
                <Table.Th>{t('serviceCatalogue.columns.check')}</Table.Th>
                <Table.Th>{t('serviceCatalogue.columns.status')}</Table.Th>
                <Table.Th aria-label={t('serviceCatalogue.columns.actions')} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((pkg) => {
                const name = entryName(pkg, i18n.language)
                const checks = pkg.positions.filter((position) => position.checkNeeded).length
                return (
                  <Table.Tr key={pkg.id}>
                    <Table.Td>
                      <Text fz="sm" fw={500}>
                        {name}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {t('serviceCatalogue.positionCount', {
                        count: pkg.positions.length,
                        labour: pkg.positions.filter((position) => position.kind === 'labour').length,
                      })}
                    </Table.Td>
                    <Table.Td>{timeText(pkg)}</Table.Td>
                    <Table.Td>{checks > 0 ? <Badge tone="purple">{t('serviceCatalogue.checkBadge')}</Badge> : '–'}</Table.Td>
                    <Table.Td>
                      {pkg.isActive ? (
                        <Badge tone="success">{t('serviceCatalogue.status.active')}</Badge>
                      ) : (
                        <Badge tone="slate">{t('serviceCatalogue.status.inactive')}</Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {canWrite && (
                        <Group gap="xs" justify="flex-end" wrap="nowrap">
                          <Button
                            size="compact-xs"
                            variant="default"
                            aria-label={t('serviceCatalogue.editNamed', { name })}
                            onClick={() => setDialog({ pkg })}
                          >
                            {t('serviceCatalogue.edit')}
                          </Button>
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            aria-label={t(pkg.isActive ? 'serviceCatalogue.deactivateNamed' : 'serviceCatalogue.activateNamed', {
                              name,
                            })}
                            onClick={() => void toggleActive(pkg)}
                          >
                            {t(pkg.isActive ? 'serviceCatalogue.deactivate' : 'serviceCatalogue.activate')}
                          </Button>
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
      {dialog && (
        <ServicePackageDialog
          pkg={dialog.pkg}
          categories={categories.data?.items ?? []}
          unitInForce={unitInForce}
          onClose={() => setDialog(null)}
          onSave={(body) => save(dialog.pkg, body)}
        />
      )}
    </Stack>
  )
}
