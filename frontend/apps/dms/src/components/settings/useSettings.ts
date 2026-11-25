import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type {
  AreaSettingsRead,
  SettingAreaList,
  SettingChangePage,
  SettingSlotRead,
  SettingValueBody,
} from '../../api/types'

/** KAN-805 — the settings store's reads and writes for the settings screen. */

const HISTORY_PAGE = 20

export const settingAreasKey = (dealershipId: string) => ['dealerships', dealershipId, 'setting-areas'] as const
export const settingAreaKey = (dealershipId: string, area: string) =>
  ['dealerships', dealershipId, 'setting-areas', area] as const
export const settingHistoryKey = (dealershipId: string, key: string, locationId: string | null) =>
  ['dealerships', dealershipId, 'setting-history', key, locationId ?? 'dealership'] as const

const base = (dealershipId: string) => `/dealerships/${dealershipId}`

export function useSettingAreas(dealershipId: string) {
  return useQuery({
    queryKey: settingAreasKey(dealershipId),
    queryFn: () => api.get<SettingAreaList>(`${base(dealershipId)}/setting-areas`),
    enabled: dealershipId !== '',
  })
}

export function useSettingArea(dealershipId: string, area: string) {
  return useQuery({
    queryKey: settingAreaKey(dealershipId, area),
    queryFn: () => api.get<AreaSettingsRead>(`${base(dealershipId)}/setting-areas/${encodeURIComponent(area)}`),
    enabled: dealershipId !== '' && area !== '',
  })
}

export function useSettingHistory(dealershipId: string, key: string, locationId: string | null) {
  return useInfiniteQuery({
    queryKey: settingHistoryKey(dealershipId, key, locationId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(HISTORY_PAGE) })
      if (locationId) params.set('locationId', locationId)
      if (pageParam) params.set('cursor', pageParam)
      return api.get<SettingChangePage>(
        `${base(dealershipId)}/setting-values/${encodeURIComponent(key)}/history?${params.toString()}`,
      )
    },
    getNextPageParam: (page) => page.nextCursor ?? null,
  })
}

/** Writes one slot under If-Match on its version (0: never written); a null
 * value clears it. */
export function writeSetting(
  dealershipId: string,
  key: string,
  locationId: string | null,
  value: SettingValueBody | null,
  version: number,
): Promise<SettingSlotRead> {
  const path = `${base(dealershipId)}/setting-values/${encodeURIComponent(key)}${locationId ? `/locations/${locationId}` : ''}`
  return api.put<SettingSlotRead>(path, { value }, { 'If-Match': String(version) })
}

/** Brings an earlier value back as a change of its own (If-Match on the slot). */
export function restoreSettingChange(dealershipId: string, changeId: string, version: number): Promise<SettingSlotRead> {
  return api.post<SettingSlotRead>(`${base(dealershipId)}/setting-changes/${changeId}/restore`, undefined, {
    'If-Match': String(version),
  })
}

/** After a write: the area (every slot, `showsLocations`) and the slot's history. */
export function useRefreshSettings(dealershipId: string) {
  const queryClient = useQueryClient()
  return async (area: string, key: string, locationId: string | null) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: settingAreaKey(dealershipId, area) }),
      queryClient.invalidateQueries({ queryKey: settingHistoryKey(dealershipId, key, locationId) }),
    ])
  }
}
