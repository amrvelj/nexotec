"""What the identAPI's valuation needs to know about a configured car
(KAN-552) — read by Valuation through `vehicle.public`, never written.

The provider values a catalogue variant by its FzKey **at the Betrieb's own
provider** (the demo provider's keys are not auto-i's), a model year, the
first registration, the mileage and the OptKeys of the options the car
carries. A configuration without a catalogue variant (manual, ADR-069), or
whose variant the connected provider never named, cannot be valued: 422
`no_catalogue_variant`.

The configuration stores no model year: it is the first registration's year,
held inside the variant's model-year range (the identAPI refuses a model year
more than two years from the registration).
"""

import uuid

from sqlalchemy.orm import Session

from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.services.catalogue_details import provider_key_of
from app.vehicle.services.catalogue_sync import find_enabled_vehicle_data_connection
from app.vehicle.services.configuration import get_configuration_or_404
from app.vehicle.services.host_configuration import NoCatalogueVariantError, ProviderValuationInput

_OPT_KEY_DIGITS = 6


def provider_valuation_input(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID
) -> ProviderValuationInput | None:
    """`None` when the Betrieb has no enabled vehicle-data connection; 404 for
    another Betrieb's configuration (rule 7); 422 `no_catalogue_variant` when
    the car has no FzKey at the connected provider."""

    configuration = get_configuration_or_404(db, tenant_id=tenant_id, configuration_id=configuration_id)
    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return None
    connection, provider_code = found
    variant = db.get(ModelVariant, configuration.catalogue_variant_id) if configuration.catalogue_variant_id else None
    fz_key = provider_key_of(db, variant=variant, provider_code=provider_code) if variant is not None else None
    if variant is None or fz_key is None:
        raise NoCatalogueVariantError()
    option_keys = [
        int(option.option_code)
        for option in configuration.options
        if option.selected
        and not option.is_included
        and option.option_code is not None
        and option.option_code.isdigit()
        and len(option.option_code) == _OPT_KEY_DIGITS
    ]
    return ProviderValuationInput(
        connection=connection,
        configuration_id=configuration.id,
        configuration_label=configuration.catalogue_variant_label,
        fz_key=fz_key,
        model_year_from=variant.model_year_from,
        model_year_to=variant.model_year_to,
        first_registration_date=configuration.first_registration_date,
        mileage_km=configuration.mileage_km,
        option_keys=option_keys,
    )
