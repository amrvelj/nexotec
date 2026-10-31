import type { TFunction } from 'i18next'
import type { HolidayCalendarRead, HolidayDateRead, LocationDepartment, LocationRead, LocationType } from '../../api/types'
import type { SupportedLanguage } from '../../i18n'

/** KAN-204 — TanStack Query keys of the Locations screens. */
export const locationsKey = (dealershipId: string) => ['dealership', dealershipId, 'locations'] as const
export const locationKey = (dealershipId: string, locationId: string) => ['dealership', dealershipId, 'locations', locationId] as const
export const openingHoursKey = (dealershipId: string, locationId: string) => [...locationKey(dealershipId, locationId), 'opening-hours'] as const
export const closuresKey = (dealershipId: string, locationId: string) => [...locationKey(dealershipId, locationId), 'closures'] as const
export const holidayCalendarsKey = ['holiday-calendars'] as const
export const holidayDatesKey = (calendarId: string, year: number) => ['holiday-calendars', calendarId, 'dates', year] as const

export const LOCATION_TYPES: LocationType[] = ['showroom', 'workshop', 'body_shop', 'parts_warehouse', 'storage', 'admin']
export const DEPARTMENTS: LocationDepartment[] = ['sales', 'service', 'parts']
/** ISO weekdays, 1 = Monday … 7 = Sunday (the API's numbering). */
export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const

export const locationTypeOptions = (t: TFunction) => LOCATION_TYPES.map((value) => ({ value, label: t(`locations.types.${value}`) }))

/** A calendar's or a holiday's label in the UI language (reference data is translated by us, ADR-044). */
export function labelIn(row: { labelDe: string; labelFr: string; labelIt: string; labelEn: string }, language: string): string {
  switch (language as SupportedLanguage) {
    case 'fr':
      return row.labelFr
    case 'it':
      return row.labelIt
    case 'en':
      return row.labelEn
    default:
      return row.labelDe
  }
}

/** The holiday-calendar picker's options, grouped by canton. */
export function calendarOptions(calendars: HolidayCalendarRead[], language: string) {
  const groups = new Map<string, { value: string; label: string }[]>()
  for (const calendar of calendars) {
    const items = groups.get(calendar.canton) ?? []
    items.push({ value: calendar.id, label: labelIn(calendar, language) })
    groups.set(calendar.canton, items)
  }
  return [...groups.entries()].map(([group, items]) => ({ group, items }))
}

export function holidayLabel(row: HolidayDateRead, language: string): string {
  return labelIn(row, language)
}

/** "Thurgauerstrasse 40, 8050 Zürich" — the parts the row has. */
export function addressLine(location: Pick<LocationRead, 'addressStreet' | 'addressHouseNumber' | 'addressPostalCode' | 'addressLocality'>): string {
  const street = [location.addressStreet, location.addressHouseNumber].filter(Boolean).join(' ')
  const locality = [location.addressPostalCode, location.addressLocality].filter(Boolean).join(' ')
  return [street, locality].filter(Boolean).join(', ')
}

/** OpenStreetMap at the pin — a link, no map tiles loaded into the app. */
export function mapUrl(lat: number, lng: number): string {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`
}
