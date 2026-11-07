import type { StockItemRead } from '../../api/types'

/** KAN-572: a pipeline item a contract's confirmation created for its
 * trade-in (`pipelineRef` `contract:<id>:trade_in`, services/pipeline.py) —
 * the one kind of car that can go back to a customer. */
export function isTradeInStockItem(item: Pick<StockItemRead, 'pipelineRef'>): boolean {
  return /^contract:[^:]+:trade_in$/.test(item.pipelineRef ?? '')
}
