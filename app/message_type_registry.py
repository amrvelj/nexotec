"""Registers every module's contact message types with platform's send-dialog
registry (KAN-217; Dealer Administration FR-A-52). A composition root directly
under app/, like app.status_registry: the one place that names each module's
registration, so platform's registry imports no module and no module is
registered by an import's side effect.

Today: Sales (offer, order confirmation, sales contract). Finance, Aftersales
and the rest join here when their documents and reminders are built.

Imported by app.main (the dialog's API) and app.worker (the send consumers and
reminders to come). Registering twice is harmless: the registry accepts the
same definition again.
"""

from app.sales.public import register_message_types as register_sales_message_types


def register_message_types() -> None:
    register_sales_message_types()


register_message_types()
