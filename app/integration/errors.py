"""The failure vocabulary of a provider connection.

A leaf on purpose: it imports nothing from this context, so the gateway, the
resilience layer and every adapter can raise or subclass these without a cycle.
`ProviderGatewayError` used to live in `services/gateway.py`, and the gateway
imports the adapters — so an adapter could not import it, and every failure a
real adapter raised (an empty response, a maintenance window, a transport
error) was a type the gateway had no way to name. `gateway.test_connection`
caught `ProviderGatewayError` only, and a wrong password reached the dealer as
an HTTP 500.

The rule this module exists to keep: **an adapter translates what its
third-party client throws into one of these, at the one place it calls that
client** — never a blanket `except Exception` in the gateway, which would file
programmer errors under "the connection failed".
"""


class ProviderGatewayError(Exception):
    """Base for every failure a provider connection can end a call with —
    never a bare exception reaching a caller outside this context.

    **The message is persisted and shown.** `gateway.test_connection` stores
    `str(exc)` in `integration_connection.last_error`, which every dealer
    manager can read. Raise these with text you wrote, never with `str()` of a
    foreign exception: a library's message can carry a URL, a header, or a
    server's echo of what it was sent. Chain the original with `from exc`
    instead — the traceback keeps it, the dealer never sees it.
    """


class ProviderTransportError(ProviderGatewayError):
    """A call failed below the level of anything the provider said: DNS, TLS,
    a timeout, an HTTP or SOAP fault, a WSDL that will not load. Worded from
    the original exception's CLASS NAME only, and always raised `from` it.
    """


class ProviderConfigurationError(ProviderGatewayError):
    """The connection is not configured well enough to make the call — no
    `wsdlUrl`, a credential that cannot be read. Something the dealer manager
    can fix, as opposed to a provider that is down.
    """
