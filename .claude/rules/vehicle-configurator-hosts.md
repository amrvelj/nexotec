---
paths:
  - "app/vehicle/services/host_configuration.py"
  - "app/vehicle/api/configuration.py"
  - "app/vehicle/services/configuration*.py"
  - "app/sales/services/deal_editing.py"
  - "app/inventory/services/stock_item.py"
  - "app/inventory/services/configuration_*.py"
  - "app/valuation/services/valuation.py"
  - "tests/test_configurator_*.py"
  - "tests/test_configuration*.py"
  - "frontend/apps/dms/src/components/configurator/ConfiguratorOverlay*"
  - "frontend/apps/dms/src/components/configurator/HostConfigurationCard*"
  - "frontend/apps/dms/src/components/configurator/configurationHosts*"
  - "frontend/apps/dms/src/components/configurator/configuratorOverlayProps.ts"
  - "frontend/apps/dms/src/pages/ConfiguratorHosts*"
  - "frontend/apps/dms/src/pages/OfferWorkspace.configurator*"
  - "frontend/apps/dms/src/components/stock-detail/*onfigurat*"
  - "frontend/apps/dms/src/components/stock-detail/useAttachConfiguration.tsx"
  - "frontend/apps/dms/src/pages/StockDetailPage.attachConfiguration*"
  - "frontend/apps/dms/src/pages/stock/AddToPipeline*"
---
<!-- Maintainer note. PRD-Configurator v1.4 (the host/mode matrix), ADR-083. Moved out of
vehicle-configurator.md by KAN-615 (2026-11-13) for the hosts' own files. Cut from vehicle.md
(verified against main@73baf3f) by KAN-614; not re-verified. -->

# Configurator hosts and modes (PRD v1.4, + the pending contract of ADR-083)

- Stock → add to pipeline: both (`build` factory order, `record` bought in) · Stock → «Konfiguration
  zuordnen» (no configuration yet): both, `record` first, VIN prefilled · Stock → «Konfiguration
  bearbeiten» on every item with one that no contract reserves: its own mode only, its PATCH the only
  write (a reserved item shows «Vertrag bearbeiten» disabled: a signed contract is frozen) · Offer
  and pending contract → new configuration: `build` only; → trade-in: `record`, via the valuation
  path · Valuation → `record` only.
- The entity supports both modes; **the host decides** — never bake a mode switch into the offer
  overlay. Each host enforces it at its API (422 `configuration_mode_not_allowed`) and the overlay
  takes `allowedModes`.
- Hosts read only through `vehicle.public.get_configuration_for_host` (Stock's label refresh through
  `get_configuration_labels`, labels only, in the stock item's Betrieb); a host copying itself gets
  `copy_configuration_for_host`, undone by `discard_configuration_copy`.
- The configuration PATCH does not check its host on the server. On a bought stock car (it has a
  vehicle-mdm record) the overlay leaves `vin` out of its PATCH and hands a changed VIN to the host,
  which corrects it through Stock's «VIN korrigieren» (KAN-557; the stock item, the record and the
  configuration together, `inventory-vin-correction.md`); a pipeline car's VIN is still written by the PATCH (it wins at
  booking, KAN-575). A VIN edited away from the car by any other path is reported by Stock's nightly
  reconciliation.
