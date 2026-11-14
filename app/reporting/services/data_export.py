"""Reporting's export declaration (KAN-218; FR-A-44): its one table is a
projection of the Betrieb's own stock, rebuilt from Stock's statements — the
records it copies leave with Stock's export (`stock_item`,
`stock_item_website_listing`), so it is declared, and left out with why."""

from collections.abc import Mapping

from app.core.export import TableExporter
from app.reporting.models.public_stock_listing import PublicStockListing


class ReportingExporter(TableExporter):
    context = "reporting"

    def excluded_tables(self) -> Mapping[str, str]:
        return {
            PublicStockListing.__tablename__: "A projection rebuilt from Stock's events; the listings it copies "
            "are in Stock's export (stock_item, stock_item_website_listing).",
        }
