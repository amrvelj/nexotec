import type { ColumnRegistryEntry, GridColumnDef } from '@nexotec/ui-kit'

/** The Columns panel's registry for a grid's columns (ADR-060): each
 * column's id, its plain-string label, whether it shows by default and
 * whether it can be hidden — the same reading `DataGrid` makes of them. */
export function columnRegistryOf<T>(columns: GridColumnDef<T>[]): ColumnRegistryEntry[] {
  return columns.map((column) => {
    const id = String(column.id)
    return {
      id,
      label: column.meta?.columnLabel ?? (typeof column.header === 'string' ? column.header : id),
      defaultVisible: column.meta?.defaultVisible ?? true,
      locked: column.meta?.locked,
    }
  })
}
