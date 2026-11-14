"""The account identifier on a connection's card (KAN-222, FR-A-20).

A dealer with four auto-i-dat accounts that sees «auto-i-dat ✓» four times
learns nothing; each card names the account at the provider. Each provider
derives it from the connection's **non-secret** configuration — never from a
secret slot, which this module cannot reach (it reads `config` only, and no
secret is ever stored there: `integration_secret_ref` holds the reference,
the value lives in the secrets manager).

- `auto_i_dat` — the identAPI service account's `clientId` (KAN-488; the
  SOAP account's BenutzerNr + BenutzerInfo left with the SOAP adapter,
  KAN-550).
- `auto_i_dat_mock` — the demo account's `benutzerNr` and `benutzerInfo`,
  the shape the SOAP account had, which its required configuration still
  carries.
- `autoscout24` — the AutoScout24 customer number (`kundennummer`).

A provider added to the catalogue adds its line here;
`tests/test_integration_account_identifier.py` fails for a provider with an
adapter and no line. When the configuration lacks the keys (a row created
before its provider required them), the identifier is None and the card says
«Kennung fehlt»: the dealer completes the configuration. No provider answer
on «Test connection» carries an account identifier we may store, so the test
does not fill it in.
"""

from collections.abc import Mapping

# provider_code -> the config keys that together name the account, in order.
ACCOUNT_IDENTIFIER_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "auto_i_dat": ("clientId",),
    "auto_i_dat_mock": ("benutzerNr", "benutzerInfo"),
    "autoscout24": ("kundennummer",),
}

_SEPARATOR = " · "


def account_identifier(provider_code: str, config: Mapping[str, object] | None) -> str | None:
    """The account's identifier, or None when the provider names none or the
    configuration lacks every key. Values are joined in the declared order;
    a key with no value is left out."""

    keys = ACCOUNT_IDENTIFIER_CONFIG_KEYS.get(provider_code)
    if not keys or not config:
        return None
    parts = [str(config[key]).strip() for key in keys if config.get(key) not in (None, "")]
    parts = [part for part in parts if part]
    return _SEPARATOR.join(parts) if parts else None
