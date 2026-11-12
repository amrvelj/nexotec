"""KAN-595 — a provider text in the four languages the identAPI delivers it
in. Provider text as delivered (ADR-044): no locale key, never translated by
us, so a language the provider left empty is null and the reader falls back
to the delivered default (`description`), never to a loud marker."""

from app.core.schemas import CamelModel


class ProviderTextsRead(CamelModel):
    de: str | None = None
    fr: str | None = None
    it: str | None = None
    en: str | None = None


def provider_texts(row) -> ProviderTextsRead:
    """The four `description_<lang>` columns of an option row."""

    return ProviderTextsRead(
        de=row.description_de, fr=row.description_fr, it=row.description_it, en=row.description_en
    )
