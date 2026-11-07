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


class ProviderTimeoutError(ProviderTransportError):
    """The provider did not answer within the transport's timeout (KAN-108):
    connected and then silent, or never connected at all. Still a transport
    failure — it counts toward the circuit breaker like any other — but the
    gateway logs it as `CallStatus.TIMEOUT`, so a hanging provider can be
    told apart from one that refuses or faults.
    """


class ProviderConfigurationError(ProviderGatewayError):
    """The connection is not configured well enough to make the call — no
    `wsdlUrl`, a credential that cannot be read. Something the dealer manager
    can fix, as opposed to a provider that is down.
    """


class ProviderRejectedError(ProviderGatewayError):
    """The provider understood the request and said no to the caller's
    credentials or to the call itself — a wrong client secret, a refused
    login, an empty SOAP answer (KAN-488). Never transient: a retry with the
    same input would not help. The SOAP parser's own `ProviderRejectedError`
    is a subclass of this one, so one `except` covers both transports.
    """


class ProviderNotEntitledError(ProviderGatewayError):
    """The provider answered 403 (KAN-488): the account is valid but the
    licence does not include this operation. `gateway.test_connection`'s
    probes read it as `granted = False` for the capability the operation
    represents; a caller outside a probe treats it like any other refusal.
    `operation` is the provider's own name for what was asked.
    """

    def __init__(self, operation: str, message: str | None = None) -> None:
        self.operation = operation
        super().__init__(message or f"{operation}: the account is not entitled to this operation (HTTP 403).")


class ProviderRequestError(ProviderGatewayError):
    """The provider refused the request as malformed or unprocessable (400 or
    422 on the identAPI, with a json:api error body; KAN-488). The licence
    allowed the call and the credentials were accepted, so it says nothing
    about entitlement — an entitlement probe leaves its row untouched. The
    message carries the status and the provider's bounded `title`, never its
    free-text `detail` and never a credential.
    """


class ProviderOperationNotBuiltError(ProviderConfigurationError):
    """An operation the adapter does not implement yet — the identAPI's
    identification, browse, details, VIN and valuation methods until their
    tickets land (KAN-488). A `ProviderConfigurationError`, so the gateway
    neither logs a call that was never sent nor charges the circuit breaker
    (five plate lookups must not take a connection offline); the message names
    the ticket.
    """
