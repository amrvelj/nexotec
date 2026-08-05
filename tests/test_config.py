import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_requires_tax_id_encryption_key(monkeypatch):
    """A previous draft shipped a live Fernet key as the default for this
    setting (found in review, now burned). Construction must fail fast
    without it rather than silently falling back to a key anyone with repo
    read access already has."""
    monkeypatch.delenv("DMS_TAX_ID_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "tax_id_encryption_key" in str(exc_info.value)
