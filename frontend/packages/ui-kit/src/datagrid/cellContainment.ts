import { createContext, useContext } from "react";

/**
 * KAN-710 — true inside a kit `DataGrid` cell. A cell is a fixed-height box
 * that clips (`overflow: hidden`, one line, the row height of its density;
 * the grid's fixed row height is a ruling, not a bug), so anything a cell's
 * content shows *below itself* is cut off. A component that has more to say
 * than its one line — `InlineEditField`'s refused save and its «reload»
 * (FR-05, KAN-473) — reads this and shows it outside the cell instead (a
 * popover, in a portal), with no caller having to know where it sits.
 */
export const GridCellContext = createContext(false);

export function useInsideGridCell(): boolean {
  return useContext(GridCellContext);
}
