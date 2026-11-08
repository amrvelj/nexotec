import type { CatalogueSpecificationRead } from '../api/types'

/** KAN-211 (ADR-123): the per-detail load states a specification carries.
 * Tests of the tabs that render options, colours or images start from
 * «loaded» unless the test is about a state. */
export const detailsPresent: CatalogueSpecificationRead['details'] = {
  options: { state: 'present', loadedAt: '2026-11-08T10:00:00Z' },
  colours: { state: 'present', loadedAt: '2026-11-08T10:00:00Z' },
  tyreSpecs: { state: 'present', loadedAt: '2026-11-08T10:00:00Z' },
  images: { state: 'present', loadedAt: '2026-11-08T10:00:00Z' },
  energyLabel: { state: 'present', loadedAt: '2026-11-08T10:00:00Z' },
}

export const detailsNotLoaded: CatalogueSpecificationRead['details'] = {
  options: { state: 'notLoaded' },
  colours: { state: 'notLoaded' },
  tyreSpecs: { state: 'notLoaded' },
  images: { state: 'notLoaded' },
  energyLabel: { state: 'notLoaded' },
}
