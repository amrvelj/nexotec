import { useEffect, useRef, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronsUpDown, ChevronUp, CircleAlert, MoreHorizontal } from "lucide-react";
import { Menu } from "@mantine/core";
import { purple, radius, semantic, slate, slate25, spacing, white } from "../tokens";
import { cycleSort } from "./sorting";
import "./datagrid.css";
import { ROW_HEIGHT, type Density, type EmptyStateConfig, type GridColumnDef, type SortSpec } from "./types";

type LinkLike = ComponentType<{ to: string; children?: ReactNode; style?: CSSProperties; className?: string }>;

const ACTION_COLUMN_WIDTH = 48;
const SELECTION_COLUMN_WIDTH = 40;

export interface DataGridSelectionProps {
  selectedIds: Set<string>;
  onSelectionChange: (selectedIds: Set<string>) => void;
}

export interface DataGridProps<T> {
  columns: GridColumnDef<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  sort: SortSpec[];
  onSortChange: (sort: SortSpec[]) => void;
  density: Density;
  rowHref?: (row: T) => string;
  linkComponent?: LinkLike;
  loading: boolean;
  /** A background refetch of the CURRENT page (a filter/sort change, a
   * manual refresh) — distinct from `loading` (nothing shown yet) and
   * `fetchingNextPage` (loading a page beyond what's already shown).
   * Renders as a 2px indeterminate line under the header, never a full
   * skeleton replacing rows the user can already see. */
  refetching?: boolean;
  fetchingNextPage: boolean;
  hasNextPage: boolean;
  onLoadMore: () => void;
  error?: string | null;
  onRetry?: () => void;
  total: number | null;
  totalIsEstimate: boolean;
  isFiltered: boolean;
  emptyState: EmptyStateConfig;
  emptyFilteredState?: EmptyStateConfig;
  rowActions?: (row: T) => ReactNode;
  /** Omit for a grid with no bulk actions. When present, a checkbox column
   * is pinned to the left of every other column, selection is against
   * `getRowId`, and the header checkbox toggles every currently *loaded*
   * row (not just the on-screen virtual window) — "select all N matching"
   * across pages the grid hasn't fetched yet is SelectionBar's own concern
   * (§ Action Bar — selection replaces the chip row), not this component's. */
  selection?: DataGridSelectionProps;
  /** Swiss locale tag (de-CH/fr-CH/it-CH/en-CH) for the footer's row-count
   * formatting — apostrophe thousands separator per FR-13. Defaults to the
   * browser locale for screens that haven't adopted i18n yet. */
  locale?: string;
  /** Translated overrides for the handful of strings this generic grid
   * owns itself (footer count, retry, loading-more, row-actions trigger).
   * Defaults to the current English copy — optional so untranslated
   * screens are unaffected. */
  labels?: {
    showing?: (shown: number) => string;
    showingOfTotal?: (shown: number, total: string) => string;
    loadingMore?: string;
    retry?: string;
    rowActionsLabel?: string;
  };
}

const DEFAULT_GRID_LABELS = {
  showing: (shown: number) => `Showing ${shown}`,
  showingOfTotal: (shown: number, total: string) => `Showing ${shown} of ${total}`,
  loadingMore: "Loading…",
  retry: "Retry",
  rowActionsLabel: "Row actions",
};

/**
 * § Overviews — The Data Grid. "The single most important pattern in the
 * DMS." TanStack Table (headless, column model) + TanStack Virtual
 * (windowed rendering) rendered entirely with our own Mantine/token-based
 * markup, per the doc's stack decision.
 */
export function DataGrid<T>({
  columns,
  rows,
  getRowId,
  sort,
  onSortChange,
  density,
  rowHref,
  linkComponent,
  loading,
  refetching,
  fetchingNextPage,
  hasNextPage,
  onLoadMore,
  error,
  onRetry,
  total,
  totalIsEstimate,
  isFiltered,
  emptyState,
  emptyFilteredState,
  rowActions,
  selection,
  locale,
  labels,
}: DataGridProps<T>) {
  const L = { ...DEFAULT_GRID_LABELS, ...labels };
  const Link = linkComponent;
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowHeight = ROW_HEIGHT[density];

  let allColumns: GridColumnDef<T>[] = columns;
  if (selection) {
    allColumns = [
      { id: "__select", header: "", cell: () => null, meta: { pinned: "left" } },
      ...allColumns,
    ];
  }
  if (rowActions) {
    allColumns = [...allColumns, { id: "__actions", header: "", cell: () => null, meta: { pinned: "right" } }];
  }

  const table = useReactTable({
    data: rows,
    columns: allColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
  });

  const tableRows = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 10,
  });

  // TanStack Virtual caches each index's measured/estimated size and
  // doesn't retroactively re-run estimateSize for already-seen indices
  // just because a re-render changed what it would now return — density
  // changing row height needs an explicit re-measure, or rows keep their
  // old height until they scroll out of and back into view.
  useEffect(() => {
    virtualizer.measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [density]);

  // "The next page is prefetched when the user scrolls within 10 rows of
  // the end of the loaded set" (FR-UI-04).
  const virtualItems = virtualizer.getVirtualItems();
  const lastItem = virtualItems.at(-1);
  useEffect(() => {
    if (!lastItem) return;
    if (lastItem.index >= tableRows.length - 10 && hasNextPage && !fetchingNextPage && !loading) {
      onLoadMore();
    }
  }, [lastItem?.index, tableRows.length, hasNextPage, fetchingNextPage, loading, onLoadMore]);

  const showEmptyFiltered = isFiltered && rows.length === 0 && !loading && !error;
  const showEmpty = !isFiltered && rows.length === 0 && !loading && !error;

  // Selection is against every currently *loaded* row (`rows`), not just
  // the virtualized on-screen window — scrolling shouldn't change what
  // "select all" means.
  const allLoadedIds = rows.map(getRowId);
  const allSelected = selection ? allLoadedIds.length > 0 && allLoadedIds.every((id) => selection.selectedIds.has(id)) : false;
  const someSelected = selection ? allLoadedIds.some((id) => selection.selectedIds.has(id)) : false;
  const toggleAll = () => {
    if (!selection) return;
    selection.onSelectionChange(allSelected ? new Set() : new Set(allLoadedIds));
  };
  const toggleOne = (id: string) => {
    if (!selection) return;
    const next = new Set(selection.selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selection.onSelectionChange(next);
  };

  // § FR-UI-04 / Keyboard: Shift+wheel scrolls the grid horizontally even
  // on platforms/browsers that don't translate it natively, and Home/End
  // (bubbling up from any focused header cell) jump to the first/last
  // column — both act on the same scroll container the virtualizer reads.
  const onWheel = (event: React.WheelEvent) => {
    if (!event.shiftKey || !scrollRef.current) return;
    event.preventDefault();
    scrollRef.current.scrollLeft += event.deltaY;
  };
  const onGridKeyDown = (event: React.KeyboardEvent) => {
    if (!scrollRef.current) return;
    if (event.key === "Home") {
      event.preventDefault();
      scrollRef.current.scrollLeft = 0;
    } else if (event.key === "End") {
      event.preventDefault();
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        border: `1px solid ${slate[2]}`,
        borderRadius: radius.lg,
        backgroundColor: white,
        overflow: "hidden",
      }}
    >
      {refetching && <div className="dg-refetch-bar" aria-hidden="true" />}
      <div ref={scrollRef} onWheel={onWheel} onKeyDown={onGridKeyDown} style={{ overflow: "auto", maxHeight: "70vh" }}>
        {/* Virtualized rows are absolutely positioned, which real <table>
            elements can't host correctly (the browser's table layout
            algorithm fights it, and <div>/<a> aren't valid <tbody>
            children anyway) — a div grid with ARIA table roles keeps the
            same semantics/accessibility without that conflict, the
            standard pattern for a virtualized table. */}
        <div role="table" aria-rowcount={total ?? rows.length} style={{ width: "100%" }}>
          <div role="rowgroup" style={{ position: "sticky", top: 0, zIndex: 2 }}>
            {table.getHeaderGroups().map((headerGroup) => (
              <div role="row" key={headerGroup.id} style={{ display: "flex" }}>
                {headerGroup.headers.map((header) => {
                  const meta = header.column.columnDef.meta as GridColumnDef<T>["meta"];
                  const isActions = header.column.id === "__actions";
                  const isSelect = header.column.id === "__select";
                  if (isSelect) {
                    return (
                      <SelectHeaderCell
                        key={header.id}
                        checked={allSelected}
                        indeterminate={someSelected && !allSelected}
                        onChange={toggleAll}
                      />
                    );
                  }
                  return (
                    <HeaderCell
                      key={header.id}
                      label={isActions ? "" : flexRender(header.column.columnDef.header, header.getContext())}
                      sortField={meta?.sortField}
                      sort={sort}
                      onSortChange={onSortChange}
                      pinned={isActions ? "right" : meta?.pinned}
                      align={meta?.align}
                      width={isActions ? ACTION_COLUMN_WIDTH : undefined}
                    />
                  );
                })}
              </div>
            ))}
          </div>
          <div role="rowgroup" style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {loading
              ? Array.from({ length: 8 }).map((_, i) => (
                  <SkeletonRow key={`initial-skeleton-${i}`} height={rowHeight} top={i * rowHeight} columns={allColumns.length} />
                ))
              : virtualItems.map((virtualRow) => {
                  const row = tableRows[virtualRow.index];
                  const href = rowHref?.(row.original);
                  const rowId = getRowId(row.original);
                  const isSelected = selection?.selectedIds.has(rowId) ?? false;
                  return (
                    <Row
                      key={row.id}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        height: virtualRow.size,
                        transform: `translateY(${virtualRow.start}px)`,
                        backgroundColor: isSelected ? purple[0] : undefined,
                      }}
                      href={href}
                      Link={Link}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const meta = cell.column.columnDef.meta as GridColumnDef<T>["meta"];
                        const isActions = cell.column.id === "__actions";
                        const isSelect = cell.column.id === "__select";
                        if (isSelect) {
                          return (
                            <Cell key={cell.id} pinned="left" width={SELECTION_COLUMN_WIDTH}>
                              <SelectRowCheckbox
                                checked={isSelected}
                                label={L.rowActionsLabel}
                                onChange={() => toggleOne(rowId)}
                              />
                            </Cell>
                          );
                        }
                        return (
                          <Cell key={cell.id} pinned={isActions ? "right" : meta?.pinned} mono={meta?.mono} align={meta?.align} width={isActions ? ACTION_COLUMN_WIDTH : undefined}>
                            {isActions && rowActions ? (
                              <RowActionsMenu ariaLabel={L.rowActionsLabel}>{rowActions(row.original)}</RowActionsMenu>
                            ) : density === "default" && meta?.secondary ? (
                              // § Composite cells: at `default` density the
                              // second fact sits on the SAME line as the
                              // first — only `comfortable` stacks them, and
                              // `compact` drops the secondary line entirely.
                              <div style={{ display: "flex", alignItems: "baseline", gap: spacing.xs, minWidth: 0 }}>
                                <span
                                  style={{
                                    fontSize: 14,
                                    color: slate[9],
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }}
                                >
                                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                </span>
                                <span
                                  style={{
                                    fontSize: 12,
                                    color: slate[5],
                                    flexShrink: 0,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }}
                                >
                                  {meta.secondary(row.original)}
                                </span>
                              </div>
                            ) : (
                              <>
                                <div style={{ fontSize: 14, color: slate[9] }}>
                                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                </div>
                                {density === "comfortable" && meta?.secondary && (
                                  <div style={{ fontSize: 12, color: slate[5] }}>{meta.secondary(row.original)}</div>
                                )}
                              </>
                            )}
                          </Cell>
                        );
                      })}
                    </Row>
                  );
                })}
            {fetchingNextPage &&
              Array.from({ length: 3 }).map((_, i) => (
                <SkeletonRow
                  key={`next-skeleton-${i}`}
                  height={rowHeight}
                  top={virtualizer.getTotalSize() + i * rowHeight}
                  columns={allColumns.length}
                />
              ))}
          </div>
        </div>

        {error && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.sm,
              padding: spacing.lg,
              color: semantic.destructive.text,
              fontSize: 14,
            }}
          >
            <CircleAlert size={18} />
            <span>{error}</span>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                style={{
                  marginLeft: "auto",
                  border: `1px solid ${slate[2]}`,
                  borderRadius: radius.sm,
                  padding: `${spacing.xs} ${spacing.sm}`,
                  background: white,
                  cursor: "pointer",
                  fontSize: 13,
                }}
              >
                {L.retry}
              </button>
            )}
          </div>
        )}

        {showEmpty && <EmptyState config={emptyState} />}
        {showEmptyFiltered && <EmptyState config={emptyFilteredState ?? emptyState} />}
      </div>

      <div
        style={{
          height: 36,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `0 ${spacing.md}`,
          borderTop: `1px solid ${slate[2]}`,
          backgroundColor: slate25,
          fontSize: 12,
          color: slate[5],
        }}
      >
        <span>
          {total === null
            ? L.showing(rows.length)
            : L.showingOfTotal(rows.length, `${total.toLocaleString(locale)}${totalIsEstimate ? "+" : ""}`)}
        </span>
        {fetchingNextPage && <span>{L.loadingMore}</span>}
      </div>
    </div>
  );
}

function HeaderCell({
  label,
  sortField,
  sort,
  onSortChange,
  pinned,
  align,
  width,
}: {
  label: ReactNode;
  sortField?: string;
  sort: SortSpec[];
  onSortChange: (sort: SortSpec[]) => void;
  pinned?: "left" | "right";
  align?: "left" | "right";
  width?: number;
}) {
  const sortIndex = sortField ? sort.findIndex((s) => s.field === sortField) : -1;
  const isSorted = sortIndex !== -1;
  const direction = isSorted ? sort[sortIndex].direction : null;

  const handleActivate = (additive: boolean) => {
    if (!sortField) return;
    onSortChange(cycleSort(sort, sortField, additive));
  };

  // § U-10: sortability is separate from visibility. Only a column with a
  // `sortField` is sortable at all — everything else renders no sort
  // affordance and no `aria-sort`, rather than an inert "none" that
  // implies clicking might do something.
  const ariaSort = !sortField ? undefined : isSorted ? (direction === "asc" ? "ascending" : "descending") : "none";

  return (
    <div
      role="columnheader"
      aria-sort={ariaSort}
      style={{
        position: pinned ? "sticky" : undefined,
        left: pinned === "left" ? 0 : undefined,
        right: pinned === "right" ? 0 : undefined,
        zIndex: pinned ? 3 : undefined,
        width: width ?? "1%",
        flex: width ? `0 0 ${width}px` : "1 1 0",
        minWidth: width ?? 120,
        textAlign: align ?? "left",
        padding: `${spacing.sm} ${spacing.md}`,
        backgroundColor: slate25,
        borderBottom: `1px solid ${slate[2]}`,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.7px",
        textTransform: "uppercase",
        color: slate[5],
        whiteSpace: "nowrap",
        cursor: sortField ? "pointer" : undefined,
        userSelect: "none",
      }}
      tabIndex={sortField ? 0 : undefined}
      onClick={sortField ? (e) => handleActivate(e.shiftKey) : undefined}
      onKeyDown={
        sortField
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                handleActivate(e.shiftKey);
              }
            }
          : undefined
      }
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        {label}
        {sortField && (
          <>
            {isSorted ? (
              direction === "asc" ? (
                <ChevronUp size={14} color={purple[6]} />
              ) : (
                <ChevronDown size={14} color={purple[6]} />
              )
            ) : (
              <ChevronsUpDown size={14} color={slate[4]} />
            )}
            {sort.length > 1 && isSorted && (
              <span style={{ fontSize: 9, fontWeight: 700, color: purple[6] }}>{sortIndex + 1}</span>
            )}
          </>
        )}
      </span>
    </div>
  );
}

function Row({
  children,
  style,
  href,
  Link,
}: {
  children: ReactNode;
  style: CSSProperties;
  href?: string;
  Link?: LinkLike;
}) {
  const rowStyle: CSSProperties = {
    ...style,
    display: "flex",
    alignItems: "center",
    borderBottom: `1px solid ${slate[1]}`,
  };
  // "A link inside a cell wins over the row click" (§ The Data Grid) is
  // impossible if the row itself IS the `<a>` — a real link rendered by a
  // cell's own `cell` renderer would then be an invalid `<a>` nested inside
  // another `<a>`, and the browser resolves that by ignoring the inner one
  // entirely. Instead the row-level link is a plain sibling, absolutely
  // positioned to cover the row and placed FIRST in the DOM (`.dg-row-link`
  // in datagrid.css); cell content renders after it, unpositioned, so it
  // stays out of that stacking level UNLESS a specific cell explicitly
  // opts a real link/button into it with its own `position: relative` —
  // exactly the escape hatch a future "this cell IS a link" column needs.
  if (href) {
    const Component = Link ?? "a";
    return (
      <div role="row" style={rowStyle} className="dg-row">
        <Component to={href} href={href} tabIndex={-1} aria-hidden="true" className="dg-row-link" />
        {children}
      </div>
    );
  }
  return (
    <div role="row" style={rowStyle} className="dg-row">
      {children}
    </div>
  );
}

function SelectHeaderCell({
  checked,
  indeterminate,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <div
      role="columnheader"
      style={{
        position: "sticky",
        left: 0,
        zIndex: 3,
        width: SELECTION_COLUMN_WIDTH,
        flex: `0 0 ${SELECTION_COLUMN_WIDTH}px`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: slate25,
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      <input type="checkbox" checked={checked} ref={inputRef} onChange={onChange} aria-label="Select all" />
    </div>
  );
}

function SelectRowCheckbox({ checked, label, onChange }: { checked: boolean; label: string; onChange: () => void }) {
  // Sits in a pinned (`position: sticky`) Cell, which already paints above
  // `.dg-row-link` — see Row's own comment. `stopPropagation` is a
  // defensive backstop, not what makes this clickable.
  return <input type="checkbox" checked={checked} onChange={onChange} aria-label={label} onClick={(e) => e.stopPropagation()} />;
}

function Cell({
  children,
  pinned,
  mono,
  align,
  width,
}: {
  children: ReactNode;
  pinned?: "left" | "right";
  mono?: boolean;
  align?: "left" | "right";
  width?: number;
}) {
  return (
    <div
      role="cell"
      style={{
        position: pinned ? "sticky" : undefined,
        left: pinned === "left" ? 0 : undefined,
        right: pinned === "right" ? 0 : undefined,
        backgroundColor: pinned ? white : undefined,
        width: width ?? "1%",
        flex: width ? `0 0 ${width}px` : "1 1 0",
        minWidth: width ?? 120,
        padding: `${spacing.sm} ${spacing.md}`,
        fontFamily: mono ? "ui-monospace, SF Mono, Menlo, monospace" : undefined,
        textAlign: align ?? "left",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
}

function SkeletonRow({ height, top, columns }: { height: number; top: number; columns: number }) {
  return (
    <div
      style={{
        position: "absolute",
        top,
        left: 0,
        width: "100%",
        height,
        display: "flex",
        alignItems: "center",
        gap: spacing.md,
        padding: `0 ${spacing.md}`,
        borderBottom: `1px solid ${slate[1]}`,
      }}
    >
      {Array.from({ length: Math.min(columns, 5) }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 12,
            flex: 1,
            maxWidth: 160,
            borderRadius: radius.sm,
            backgroundColor: slate[1],
          }}
        />
      ))}
    </div>
  );
}

function EmptyState({ config }: { config: EmptyStateConfig }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: spacing.sm,
        padding: `${spacing.xl} ${spacing.xl} 60px`,
        textAlign: "center",
      }}
    >
      <div style={{ color: slate[4] }}>{config.icon}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: slate[9] }}>{config.title}</div>
      <div style={{ fontSize: 14, color: slate[5], maxWidth: 360 }}>{config.description}</div>
      {config.action}
    </div>
  );
}

function RowActionsMenu({ children, ariaLabel }: { children: ReactNode; ariaLabel: string }) {
  return (
    <Menu shadow="md" width={200} position="bottom-end" withinPortal>
      <Menu.Target>
        <button
          type="button"
          aria-label={ariaLabel}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          style={{
            border: "none",
            background: "none",
            cursor: "pointer",
            color: slate[4],
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: radius.sm,
          }}
        >
          <MoreHorizontal size={18} />
        </button>
      </Menu.Target>
      <Menu.Dropdown onClick={(e) => e.stopPropagation()}>{children}</Menu.Dropdown>
    </Menu>
  );
}
