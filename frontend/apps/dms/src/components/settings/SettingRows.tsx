import { Fragment, useState } from 'react'
import { ActionIcon, Paper, Stack, Table, Text, Tooltip } from '@mantine/core'
import { History } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@nexotec/ui-kit'
import type { AreaSettingRead, AreaSettingsRead, SettingSlotRead } from '../../api/types'
import { useCanWrite } from '../../auth/useCanWrite'
import { SettingCell } from './SettingCell'
import { SettingHistory, type HistoryScope } from './SettingHistory'
import { numberRuleAt } from './numberRules'
import { settingLabel } from './settingValue'
import { useRefreshSettings, writeSetting } from './useSettings'

export interface SettingRowsProps {
  dealershipId: string
  /** The area's settings as `GET …/setting-areas/{area}` returns them. */
  data: AreaSettingsRead
  /** Only these keys, in this order — a topic screen (e.g. «Stundenansätze»)
   * embedding the settings it holds (ticket criterion 6, 2026-11-25). All
   * of the area's when omitted. */
  keys?: string[]
  onReload: () => void
}

const neverWritten = (locationId: string): SettingSlotRead => ({
  locationId,
  isSet: false,
  value: null,
  version: 0,
  changedAt: null,
  changedBy: null,
  fitsDeclaration: true,
})

/**
 * KAN-805 — the settings screen pattern's one row component: a setting per
 * row, the Betrieb's value, and a column per location while the API says so
 * (`showsLocations`: more than one active location, or any location holding
 * a value of its own — Anto, 2026-11-24). The area screen renders every
 * setting of an area; a topic screen renders the ones it holds, so both show
 * and change the same value and the same history. The history opens under
 * its row. The manager writes; every role reads (`dealership_settings`).
 */
export function SettingRows({ dealershipId, data, keys, onReload }: SettingRowsProps) {
  const { t } = useTranslation()
  const canWrite = useCanWrite('dealership_settings')
  const refresh = useRefreshSettings(dealershipId)
  const [openHistory, setOpenHistory] = useState<string | null>(null)

  const settings = keys
    ? keys.flatMap((key) => data.settings.filter((setting) => setting.declaration.key === key))
    : data.settings
  // A location's column: active ones, and a deactivated one still holding a
  // value of its own (read-only — the API refuses writes to it).
  // As the API's `showsLocations` counts: a value in force, or a stored one that no longer fits.
  const holdsValue = (locationId: string) =>
    data.settings.some((setting) =>
      setting.locations.some((slot) => slot.locationId === locationId && (slot.isSet || !slot.fitsDeclaration)),
    )
  const locations = data.showsLocations
    ? data.locations.filter((location) => location.isActive || holdsValue(location.id))
    : []
  const slotAt = (setting: AreaSettingRead, locationId: string) =>
    setting.locations.find((slot) => slot.locationId === locationId) ?? neverWritten(locationId)
  // KAN-806 (check 49): a choice that needs another setting is unavailable where that one is
  // not in force — the location's own value, else the Betrieb's, else what «not set» acts as —
  // as the API would refuse it (`setting_rules._effective`). A required key of another area is
  // not in this area's data: the choice stays offered and the API's refusal is shown.
  const inForceAt = (key: string, locationId: string | null): boolean => {
    const other = data.settings.find((setting) => setting.declaration.key === key)
    if (!other) return true
    if (other.declaration.whenNotSet === 'acts_as' && other.declaration.actsAs != null) return true
    const own = locationId === null ? null : other.locations.find((slot) => slot.locationId === locationId)
    return Boolean(own?.isSet) || other.dealership.isSet
  }
  const unavailableAt = (setting: AreaSettingRead, locationId: string | null): string[] =>
    (setting.declaration.choiceRequires ?? [])
      .filter((rule) => !inForceAt(rule.key, locationId))
      .map((rule) => rule.choice)
  // KAN-808: `divides` and `multipleOf` the same way — the other number in force in that
  // column (for the Betrieb's, also at each location taking the Betrieb's value), so O6 offers
  // only divisors of 60 that O7 stays a multiple of, and O7 takes only multiples of O6.

  const save = (setting: AreaSettingRead, locationId: string | null) => async (value: Parameters<typeof writeSetting>[3], version: number) => {
    await writeSetting(dealershipId, setting.declaration.key, locationId, value, version)
    await refresh(data.area, setting.declaration.key, locationId)
  }
  const columnCount = 3 + locations.length

  return (
    <Paper withBorder radius="md">
      <Table aria-label={t('settings.tableLabel')} verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t('settings.columns.setting')}</Table.Th>
            <Table.Th>{t('settings.columns.dealership')}</Table.Th>
            {locations.map((location) => (
              <Table.Th key={location.id}>
                {location.name}
                {!location.isActive && (
                  <Text span size="xs" c="dimmed">
                    {' '}
                    {t('settings.columns.inactive')}
                  </Text>
                )}
              </Table.Th>
            ))}
            <Table.Th aria-label={t('settings.history.title')} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {settings.map((setting) => {
            const { declaration } = setting
            const label = settingLabel(t, declaration.key)
            const historyOpen = openHistory === declaration.key
            const scopes: HistoryScope[] = [
              { locationId: null, name: t('settings.columns.dealership'), slot: setting.dealership, writable: true },
              ...locations.map((location) => ({
                locationId: location.id,
                name: location.name,
                slot: slotAt(setting, location.id),
                // A deactivated location's settings are not changed (the API's 409).
                writable: location.isActive,
              })),
            ]
            return (
              <Fragment key={declaration.key}>
                <Table.Tr>
                  <Table.Td>
                    <Stack gap={2} align="flex-start">
                      <Text size="sm" fw={500}>
                        {label}
                      </Text>
                      {declaration.checklistItem && <Badge tone="purple">{t('settings.checklistItem')}</Badge>}
                    </Stack>
                  </Table.Td>
                  <Table.Td>
                    <SettingCell
                      declaration={declaration}
                      slot={setting.dealership}
                      canWrite={canWrite}
                      unavailableChoices={unavailableAt(setting, null)}
                      numberRule={numberRuleAt(data, setting, null)}
                      onSave={save(setting, null)}
                      onReload={onReload}
                    />
                  </Table.Td>
                  {locations.map((location) => (
                    <Table.Td key={location.id}>
                      <SettingCell
                        declaration={declaration}
                        slot={slotAt(setting, location.id)}
                        inherited={setting.dealership}
                        locationName={location.name}
                        canWrite={canWrite && location.isActive}
                        unavailableChoices={unavailableAt(setting, location.id)}
                        numberRule={numberRuleAt(data, setting, location.id)}
                        onSave={save(setting, location.id)}
                        onReload={onReload}
                      />
                    </Table.Td>
                  ))}
                  <Table.Td>
                    <Tooltip label={t('settings.history.title')} withArrow>
                      <ActionIcon
                        variant={historyOpen ? 'light' : 'subtle'}
                        aria-label={`${t('settings.history.title')}: ${label}`}
                        aria-expanded={historyOpen}
                        onClick={() => setOpenHistory(historyOpen ? null : declaration.key)}
                      >
                        <History size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
                {historyOpen && (
                  <Table.Tr>
                    <Table.Td colSpan={columnCount}>
                      <SettingHistory
                        dealershipId={dealershipId}
                        declaration={declaration}
                        scopes={scopes}
                        canWrite={canWrite}
                        onRestored={(locationId) => refresh(data.area, declaration.key, locationId)}
                      />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            )
          })}
        </Table.Tbody>
      </Table>
    </Paper>
  )
}
