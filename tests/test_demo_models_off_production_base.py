"""KAN-332: the test-only DemoWidget never sits on the production Base, so
Base.metadata — what alembic/env.py, the KAN-251 schema gate and every
metadata-driven tool compare against — holds production tables only, even
inside the test process, where tests/conftest.py has imported the demo model."""

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.db import Base
from tests.demo_models import DemoBase, DemoWidget


def test_the_demo_model_is_not_on_the_production_metadata():
    assert "demo_widget" not in Base.metadata.tables
    assert DemoWidget.__table__.metadata is DemoBase.metadata
    assert not [m.class_ for m in Base.registry.mappers if m.class_.__module__.startswith("tests.")]
