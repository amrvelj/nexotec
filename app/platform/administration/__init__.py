"""Platform's `administration` sub-package (ADR-163, amended by KAN-635 PR 4): a Betrieb's
lifecycle and the operator's tools — the setup checklist and activation, the publishing profile
and its readiness, configuration copy, data export and offboarding, the stored-objects check and
the status page. It sits above `documents` and imports only itself and the sub-packages below;
only platform's wiring (`public`, `consumers`, `daily_jobs`, `reconciliation`) imports it.
"""
