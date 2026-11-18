---
paths:
  - "app/platform/**"
  - "tests/architecture/test_platform_sub_package_directions.py"
---
<!-- Maintainer note. ADR-163 as amended by KAN-635 PR 4 (the fifth sub-package, administration);
ADR-133. Written by KAN-751 (2026-11-18) for every platform module. Last verified against
main@3b2bf69 (2026-11-18). -->

# Platform's sub-packages (ADR-163)

- **Platform is one bounded context in five sub-packages**, lowest first: `reference` ←
  `organisation` ← `identity` ← `documents` ← `administration`; above them the wiring (`public`,
  `consumers`, `daily_jobs`, `reconciliation`, `health`, the package `__init__`) may import every
  layer. A module imports platform code from its own sub-package or a lower one only — one
  migration chain, one `public.py`. `tests/architecture/test_platform_sub_package_directions.py`
  enforces it with no suppression and fails on a new top-level module it cannot place: an import
  that reaches up means the module belongs higher, or what it needs belongs lower.
- New platform code goes into the sub-package whose `__init__.py` docstring names its facts:
  reference — reference data, UI translations, the mail and Web Push ports; organisation — dealer
  groups, Betriebe, locations, schedules, holidays, bank accounts, branding, settings,
  entitlements, number ranges, contact groups; identity — users, memberships, roles, delegations,
  credentials, sessions, tokens, MFA, recovery, connected accounts, preferences, onboarding,
  notifications, support sessions; documents — templates and rendering, letterhead, standard
  texts; administration — the setup checklist and activation, the publishing profile,
  configuration copy, export and offboarding, the stored-objects check, the status page
  (`platform-administration.md`).
- The frozen seed modules stay where the shipped migrations import them (ADR-133):
  `reference_data_seed` counts as reference, `services/standard_text_seed.py` as documents.
