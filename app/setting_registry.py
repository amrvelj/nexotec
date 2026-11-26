"""Registers every area's setting declarations with platform's settings store
(KAN-805, ADR-214). A composition root directly under app/, like
app.message_type_registry: the one place that names each area's
registration, so platform's store imports no area (ADR-131) and nothing is
declared by an import's side effect.

Today: the labour-rate settings O16, O18 and O19 (KAN-806; read by platform's
labour rates and by Aftersales through `platform.public`) and the planner's
grid and slot lengths O6 and O7 (KAN-808; read by planning's bookings). The
rest join here with the ticket that builds their reader — a declaration
nobody reads fails tests/architecture/test_setting_keys_declared_and_read.py.

Imported by app.main (the administration's API) and app.worker (the setup
checklist's daily job). Registering twice is harmless: the store accepts the
same declaration again.
"""


def register_settings() -> None:
    from app.planning import public as planning
    from app.platform import public as platform

    for declaration in platform.LABOUR_RATE_SETTINGS:
        platform.register_setting(declaration)
    planning.register_settings()
    # Last: every key a rule across two settings names is declared.
    platform.check_declaration_references()


register_settings()
