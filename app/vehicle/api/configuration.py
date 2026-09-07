"""Configuration endpoints (C-C / KAN-41) — `/v1/configurations`.

**No standalone surface** (ADR-068): there is **no `GET /v1/configurations`
list**, no nav entry and no human-readable number. Hosts (an offer, a
stock item, a valuation, a vehicle — wired in C-F) create a configuration,
store its `id`, and read / patch it by id.

Tenant-scoped (ADR-013): every read is filtered by `principal.tenant_id`
and a cross-tenant id is **404, never 403** (rule 7).
"""

import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotency import find_cached_response, store_response
from app.core.permissions import require_write
from app.db import get_db
from app.vehicle.schemas.configuration import (
    ConfigurationCreate,
    ConfigurationOptionRead,
    ConfigurationOptionsReplace,
    ConfigurationRead,
    ConfigurationUpdate,
)
from app.vehicle.schemas.spec_block import VehicleSpecBlockRead
from app.vehicle.services import configuration as configuration_service

router = APIRouter(tags=["configuration"])


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


def _read(config) -> ConfigurationRead:
    row = {c.name: getattr(config, c.name) for c in config.__table__.columns}
    return ConfigurationRead.model_validate(
        {
            **row,
            "spec": VehicleSpecBlockRead.model_validate(config, from_attributes=True),
            "options": [
                ConfigurationOptionRead(
                    id=o.id,
                    sequence=o.sequence,
                    variant_option_id=o.variant_option_id,
                    option_code=o.option_code,
                    description=o.description,
                    option_group=o.option_group,
                    price=o.price,
                    is_included=o.is_included,
                    is_package=o.is_package,
                    selected=o.selected,
                    equipment_features=[link.feature_value_code for link in o.equipment_feature_links],
                )
                for o in config.options
            ],
        }
    )


@router.post("/configurations", response_model=ConfigurationRead, status_code=201)
def create_configuration(
    body: ConfigurationCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    config = configuration_service.create_configuration(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, data=body
    )
    result = _read(config)

    if idempotency_key:
        store_response(
            db,
            tenant_id=principal.tenant_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.get("/configurations/{configuration_id}", response_model=ConfigurationRead)
def get_configuration(
    configuration_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    config = configuration_service.get_configuration_or_404(
        db, tenant_id=principal.tenant_id, configuration_id=configuration_id
    )
    return _read(config)


@router.patch("/configurations/{configuration_id}", response_model=ConfigurationRead)
def update_configuration(
    configuration_id: uuid.UUID,
    body: ConfigurationUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    config = configuration_service.get_configuration_or_404(
        db, tenant_id=principal.tenant_id, configuration_id=configuration_id
    )
    check_version(config.version, if_match, entity_name="VehicleConfiguration")
    config = configuration_service.update_configuration(
        db, configuration=config, actor_id=principal.user_id, data=body
    )
    return _read(config)


@router.post("/configurations/{configuration_id}/copy", response_model=ConfigurationRead, status_code=201)
def copy_configuration(
    configuration_id: uuid.UUID,
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    source = configuration_service.get_configuration_or_404(
        db, tenant_id=principal.tenant_id, configuration_id=configuration_id
    )
    copy = configuration_service.copy_configuration(db, source=source, actor_id=principal.user_id)
    return _read(copy)


@router.patch("/configurations/{configuration_id}/options", response_model=ConfigurationRead)
def replace_options(
    configuration_id: uuid.UUID,
    body: ConfigurationOptionsReplace,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("configurations")),
    db: Session = Depends(get_db),
):
    config = configuration_service.get_configuration_or_404(
        db, tenant_id=principal.tenant_id, configuration_id=configuration_id
    )
    check_version(config.version, if_match, entity_name="VehicleConfiguration")
    config = configuration_service.replace_options(
        db, configuration=config, actor_id=principal.user_id, options=body.options
    )
    return _read(config)
