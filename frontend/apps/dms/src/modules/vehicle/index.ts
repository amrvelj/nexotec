// The vehicle module's one door (ADR-161, KAN-636): the vehicle, the catalogue
// and the configurator. Production code outside src/modules/vehicle/ imports
// what it needs from here, never from a file inside; the router (App.tsx)
// lazy-loads the pages by path so each stays its own chunk, and tests may
// reach in to render what they test. A door never names a page (rule 4): the
// configurator overlay and the cards its hosts (stock, valuation, the deal
// workspace) show live in components/configurator/, and the plate holders a
// refused invoice request names in components/vehicle-detail/. What the shell
// or several modules use is in src/shared/.
// architecture/module-boundaries.test.ts enforces it.
export { ConfigurationSummaryCard } from './components/configurator/ConfigurationSummaryCard'
export { ConfiguratorOverlay, type HostOwnedVin } from './components/configurator/ConfiguratorOverlay'
export { HostCommitError } from './components/configurator/hostCommitError'
export { HostConfigurationCard } from './components/configurator/HostConfigurationCard'
export { CANTON_CODES, holdersOf, type PlateHolder } from './components/vehicle-detail/plateRefusal'
// KAN-810: the workshop order's «Fahrzeug erfassen» is this create step, never a copy of it.
export { VehicleCreateDialog } from './components/VehicleCreateDialog'
