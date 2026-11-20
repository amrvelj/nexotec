"""Registers every context's status-page contribution with `app.core.status`
(KAN-223; Dealer Administration D-A-38, FR-A-43). A composition root directly
under app/, like app.ownership_registry: the one place that may name every
context's status provider, so that platform's status page asks the registry
and imports no context, and no context imports platform.

Today: integration's connections, and Google Business Profile, whose item
says when Nexotec's own client at Google is interrupted (KAN-776). Sending
identities are a later source and join here in a ticket of their own (Anto,
2026-11-14, KAN-223 Q1 A). Platform's own sources (number ranges, Nexotec's status) are
derived by platform itself and are not registered.

Imported by app.main (the API serves the page) and app.worker (the daily
notification job derives it). Importing it twice is harmless: the registry
replaces by source.
"""

from app.core.status import register_status_provider
from app.integration.public import GbpStatusProvider, IntegrationStatusProvider


def register_status_providers() -> None:
    for provider in (IntegrationStatusProvider(), GbpStatusProvider()):
        register_status_provider(provider)


register_status_providers()
