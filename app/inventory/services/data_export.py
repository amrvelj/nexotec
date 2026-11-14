"""Stock's export declaration (KAN-218; FR-A-44): the Betrieb's stock items,
their ledger (the Wagenbuch), options, media references and publishing, and
Stock's own copies of Sales' contracts. Every table is the Betrieb's
(`tenant_id`).
"""

from collections.abc import Mapping, Sequence

from app.core.export import ExportedTable, TableExporter, table_of
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.sales_contract import InventorySalesContract, InventorySalesContractBackfill
from app.inventory.models.stock_item import StockItem, StockNumberSequence
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_option import StockItemOption
from app.inventory.models.stock_item_publishing import StockItemMedia, StockItemPublishing


def _tenant(model) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id)


class InventoryExporter(TableExporter):
    context = "inventory"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return tuple(
            _tenant(model)
            for model in (
                StockItem,
                StockNumberSequence,
                StockItemLedger,
                StockItemOption,
                StockItemMedia,
                StockItemPublishing,
                InventorySalesContract,
                InventoryCancelledContract,
            )
        )

    def excluded_tables(self) -> Mapping[str, str]:
        return {
            InventorySalesContractBackfill.__tablename__: "A one-off migration's bookkeeping, no Betrieb's data.",
        }
