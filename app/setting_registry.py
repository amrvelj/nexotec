"""Registers every area's setting declarations with platform's settings store
(KAN-805, ADR-214). A composition root directly under app/, like
app.message_type_registry: the one place that names each area's
registration, so platform's store imports no area (ADR-131) and nothing is
declared by an import's side effect.

Today: none. Aftersales (labour rates net or with VAT, hours or work units,
KAN-806), Planning (the planner's grid and slot lengths, KAN-808) and the rest
join here with the ticket that builds their reader — a declaration nobody reads
fails tests/architecture/test_setting_keys_declared_and_read.py.

Imported by app.main (the administration's API) and app.worker (the setup
checklist's daily job). Registering twice is harmless: the store accepts the
same declaration again.
"""


def register_settings() -> None:
    return None


register_settings()
