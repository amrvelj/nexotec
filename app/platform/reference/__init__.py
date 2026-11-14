"""Platform's `reference` sub-package (ADR-163): reference data, UI translations and the
outbound ports every other sub-package uses (mail, Web Push). The lowest sub-package: it
imports nothing else of platform but the frozen `reference_data_seed` (KAN-635).
"""
