import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronsUpDown, ChevronUp, CircleAlert } from "lucide-react";
import { purple, radius, semantic, slate, slate25, spacing, white } from "../tokens";
import { RowMenu, type RowMenuGroups } from "../components/RowMenu";
import { formatNumber } from "../format/swissNumber";
import { cycleSort } from "./sorting";
import { GridCellContext } from "./cellContainment";
import { resizeColumn, resolveColumnLayout, type ColumnLayoutState, type ColumnRegistryEntry } from "./columnLayout";
import "./datagrid.css";
import { ROW_HEIGHT, type Density, type EmptyStateConfig, type GridColumnDef, type GridColumnMeta, type SortSpec } from "./types";

type LinkLike = ComponentType<{ to: string; children?: ReactNode; style?: CSSProperties; className?: string }>;

const ACTION_COLUMN_WIDTH = 48;
const SELECTION_COLUMN_WIDTH = 40;
/** The narrowest an unsized column gets — unless its heading needs more. */
const UNSIZED_MIN_WIDTH = 120;
/** A header cell's horizontal padding (`spacing.md` on each side). */
const HEADER_PADDING_X = 28;

const NO_WRAP_ELLIPSIS: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

/** § Composite cells, `default` density, one line: the primary keeps its
 * content width (capped at the cell) and does not shrink (KAN-573). */
const INLINE_COMPOSITE_PRIMARY_STYLE: CSSProperties = {
  ...NO_WRAP_ELLIPSIS,
  fontSize: 14,
  color: slate[9],
  flex: "0 0 auto",
  maxWidth: "100%",
};

/** …and the secondary takes what is left, shrinking to an ellipsis (to
 * zero width if need be) before the primary gives up anything. */
const INLINE_COMPOSITE_SECONDARY_STYLE: CSSProperties = {
  ...NO_WRAP_ELLIPSIS,
  fontSize: 12,
  color: slate[5],
  flex: "0 1 auto",
  minWidth: 0,
};

export interface DataGridSelectionProps<T = unknown> {
  selectedIds: Set<string>;
  onSelectionChange: (selectedIds: Set<string>) => void;
  /** KAN-291 — the selection header checkbox's accessible name. With the
   * selection, so a grid that selects cannot omit it (KAN-422). */
  selectAllLabel: string;
  /** KAN-313 — each row's selection checkbox's accessible name, built
   * from the row so it names the record ("Zeile auswählen: Müller
   * Hans"). Never the row menu's name: two controls on one row must not
   * share one. */
  selectRowLabel: (row: T) => string;
}

/** The strings this generic grid owns itself (footer count, retry,
 * loading-more, row-actions trigger), all translated by the caller: the
 * kit ships no English of its own (KAN-422). */
export interface DataGridLabels {
  showing: (shown: number) => string;
  showingOfTotal: (shown: number, total: string) => string;
  loadingMore: string;
  retry: string;
  rowActionsLabel: string;
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
  /** § ADR-061 — returns the row's actions grouped per the fixed contract
   * (Navigate/Edit/Create-from/Export-print/Destructive), never raw JSX;
   * `RowMenu` is what actually renders them, so the grid and a detail
   * screen's overflow render from the identical definition. */
  rowActions?: (row: T) => RowMenuGroups;
  /** Activate a row by click, Enter or Space — distinct from `rowHref`
   * (navigates) and `selection` (bulk). For a grid whose rows are *picked*
   * rather than opened: the configurator's "Find the car" browse (C-C).
   * A cell's own interactive control still wins — cells `stopPropagation`. */
  onRowActivate?: (row: T) => void;
  /** Omit for a grid with no bulk actions. When present, a checkbox column
   * is pinned to the left of every other column, selection is against
   * `getRowId`, and the header checkbox toggles every currently *loaded*
   * row (not just the on-screen virtual window) — "select all N matching"
   * across pages the grid hasn't fetched yet is SelectionBar's own concern
   * (§ Action Bar — selection replaces the chip row), not this component's. */
  selection?: DataGridSelectionProps<T>;
  /** § Columns (ADR-060) — the user's own show/hide/reorder/resize/pin
   * state, from `ColumnConfigPanel`. Omit for a grid that renders its
   * `columns` prop exactly as given, with no user layout control at all —
   * the two are fully independent; the fixed-order columns of a grid that
   * hasn't adopted this yet are completely unaffected. */
  columnLayout?: ColumnLayoutState;
  onColumnLayoutChange?: (layout: ColumnLayoutState) => void;
  labels: DataGridLabels;
  /** The grid's accessible name (its `role="table"`), in the UI language —
   * for a screen that holds more than one list, or names its list (a
   * category tab's label). Omit where the page's heading names the grid. */
  ariaLabel?: string;
  /** A row that needs attention as a whole — a reference value missing a
   * language (KAN-516): its background is the semantic destructive surface,
   * explained by the row's own status cell. A selected row keeps the
   * selection colour. Omit for a grid whose rows carry no tone. */
  rowTone?: (row: T) => "destructive" | undefined;
}

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
  onRowActivate,
  selection,
  columnLayout,
  onColumnLayoutChange,
  labels,
  ariaLabel,
  rowTone,
}: DataGridProps<T>) {
  const L = labels;
  const Link = linkComponent;
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowHeight = ROW_HEIGHT[density];

  // § Columns (ADR-060) — reorder/hide/resize/pin only ever applies on top
  // of the caller's own `columns`; a grid with no `columnLayout` renders
  // them exactly as given, unchanged from before this existed.
  let orderedColumns: GridColumnDef<T>[] = columns;
  if (columnLayout) {
    const registry: ColumnRegistryEntry[] = columns.map((c) => {
      const meta = c.meta as GridColumnMeta<T> | undefined;
      const id = String(c.id ?? ("accessorKey" in c ? c.accessorKey : ""));
      return {
        id,
        label: meta?.columnLabel ?? (typeof c.header === "string" ? c.header : id),
        defaultVisible: meta?.defaultVisible ?? true,
        locked: meta?.locked,
      };
    });
    const resolved = resolveColumnLayout(registry, columnLayout);
    const byId = new Map(columns.map((c) => [String(c.id ?? ("accessorKey" in c ? c.accessorKey : "")), c]));
    orderedColumns = resolved.visibleOrder
      .map((id) => byId.get(id))
      .filter((c): c is GridColumnDef<T> => Boolean(c))
      .map((c) => {
        const id = String(c.id ?? ("accessorKey" in c ? c.accessorKey : ""));
        const widthOverride = resolved.widths[id];
        const pinnedOverride = resolved.pinnedLeftIds.has(id) ? ("left" as const) : c.meta?.pinned;
        if (widthOverride === undefined && pinnedOverride === c.meta?.pinned) return c;
        return { ...c, meta: { ...c.meta, width: widthOverride ?? c.meta?.width, pinned: pinnedOverride } };
      });
  }

  let allColumns: GridColumnDef<T>[] = orderedColumns;
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

  // KAN-578 (Anto, 2026-11-07, option A) — a column without a width of its
  // own (`meta.width` or the user's) is never narrower than its heading, in
  // whatever language: each header reports the width its label and sort
  // indicator need, and the column's header and body cells take it as their
  // minimum. When the columns' minimums exceed the grid, the grid scrolls
  // sideways (Shift + wheel) instead of cutting a heading — before, every
  // unsized column could shrink to 120 px and «KILOMETERSTAND» was clipped.
  const [headingWidths, setHeadingWidths] = useState<Record<string, number>>({});
  const reportHeadingWidth = useCallback((columnId: string, width: number) => {
    setHeadingWidths((previous) => (previous[columnId] === width ? previous : { ...previous, [columnId]: width }));
  }, []);
  const unsizedMinWidth = (columnId: string) =>
    Math.max(UNSIZED_MIN_WIDTH, Math.ceil((headingWidths[columnId] ?? 0) + HEADER_PADDING_X));
  // The table is at least as wide as its columns' minimums together, so the
  // header and every body row (each `width: 100%` of it) span the whole
  // scrollable width: scrolled sideways, a row's divider, hover and selection
  // background and its link still reach the last column.
  const tableMinWidth = allColumns.reduce((sum, column) => {
    const id = String(column.id);
    if (id === "__actions") return sum + ACTION_COLUMN_WIDTH;
    if (id === "__select") return sum + SELECTION_COLUMN_WIDTH;
    const ownWidth = (column.meta as GridColumnDef<T>["meta"])?.width;
    return sum + (ownWidth ?? unsizedMinWidth(id));
  }, 0);

  // KAN-582 — every left-pinned column sticks *after* the left-pinned columns
  // before it, never at `left: 0` on top of them: scrolled sideways, the
  // selection checkboxes (40 px) stay reachable beside the pinned identifier,
  // and several pinned columns stand one after the other. A column's share of
  // the offset is its own width when it has one (`meta.width`, the user's
  // resize, the select column's 40 px); an unsized pinned column grows with
  // the grid (`flex: 1 1 0`), so its header reports its rendered width and
  // that is used (its minimum until the first measurement).
  const [pinnedWidths, setPinnedWidths] = useState<Record<string, number>>({});
  const reportPinnedWidth = useCallback((columnId: string, width: number) => {
    setPinnedWidths((previous) => (previous[columnId] === width ? previous : { ...previous, [columnId]: width }));
  }, []);
  const stickyLeftOffsets = new Map<string, number>();
  {
    let offset = 0;
    for (const column of allColumns) {
      const id = String(column.id);
      const meta = column.meta as GridColumnDef<T>["meta"];
      if (meta?.pinned !== "left") continue;
      stickyLeftOffsets.set(id, offset);
      offset += id === "__select" ? SELECTION_COLUMN_WIDTH : (meta.width ?? pinnedWidths[id] ?? unsizedMinWidth(id));
    }
  }

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
    // Home/End inside a field in a cell (an inline edit) move its caret, never the grid.
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable='true']")) return;
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
        <div role="table" aria-label={ariaLabel} aria-rowcount={total ?? rows.length} style={{ width: "100%", minWidth: tableMinWidth }}>
          <div role="rowgroup" style={{ position: "sticky", top: 0, zIndex: 2 }}>
            {table.getHeaderGroups().map((headerGroup) => (
              <div role="row" key={headerGroup.id} style={{ display: "flex" }}>
                {headerGroup.headers.map((header) => {
                  const meta = header.column.columnDef.meta as GridColumnDef<T>["meta"];
                  const isActions = header.column.id === "__actions";
                  const isSelect = header.column.id === "__select";
                  // The select column exists only with a `selection`.
                  if (isSelect && selection) {
                    return (
                      <SelectHeaderCell
                        key={header.id}
                        checked={allSelected}
                        indeterminate={someSelected && !allSelected}
                        label={selection.selectAllLabel}
                        onChange={toggleAll}
                      />
                    );
                  }
                  const columnId = header.column.id;
                  return (
                    <HeaderCell
                      key={header.id}
                      label={isActions ? "" : flexRender(header.column.columnDef.header, header.getContext())}
                      sortField={meta?.sortField}
                      sort={sort}
                      onSortChange={onSortChange}
                      pinned={isActions ? "right" : meta?.pinned}
                      stickyLeft={stickyLeftOffsets.get(columnId)}
                      align={meta?.align}
                      width={isActions ? ACTION_COLUMN_WIDTH : meta?.width}
                      minWidth={unsizedMinWidth(columnId)}
                      onMeasureHeading={isActions ? undefined : (width) => reportHeadingWidth(columnId, width)}
                      onMeasureWidth={
                        meta?.pinned === "left" && meta.width === undefined
                          ? (width) => reportPinnedWidth(columnId, width)
                          : undefined
                      }
                      onResize={
                        columnLayout && onColumnLayoutChange
                          ? (width) => onColumnLayoutChange(resizeColumn(columnLayout, columnId, width))
                          : undefined
                      }
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
                        backgroundColor: isSelected
                          ? purple[0]
                          : rowTone?.(row.original) === "destructive"
                            ? semantic.destructive.surface
                            : undefined,
                      }}
                      href={href}
                      Link={Link}
                      onActivate={onRowActivate ? () => onRowActivate(row.original) : undefined}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const meta = cell.column.columnDef.meta as GridColumnDef<T>["meta"];
                        const isActions = cell.column.id === "__actions";
                        const isSelect = cell.column.id === "__select";
                        if (isSelect && selection) {
                          return (
                            <SelectCell key={cell.id}>
                              <SelectRowCheckbox
                                checked={isSelected}
                                label={selection.selectRowLabel(row.original)}
                                onChange={() => toggleOne(rowId)}
                              />
                            </SelectCell>
                          );
                        }
                        return (
                          <Cell key={cell.id} pinned={isActions ? "right" : meta?.pinned} stickyLeft={stickyLeftOffsets.get(cell.column.id)} mono={meta?.mono} align={meta?.align} width={isActions ? ACTION_COLUMN_WIDTH : meta?.width} minWidth={unsizedMinWidth(cell.column.id)}>
                            {isActions && rowActions ? (
                              <RowMenu groups={rowActions(row.original)} ariaLabel={L.rowActionsLabel} />
                            ) : density !== "comfortable" && meta?.secondary ? (
                              // § Composite cells (UI/UX Specification, revised
                              // 2026-08-16; FR-UI-03): at `compact` and
                              // `default` the second fact sits on the SAME
                              // line as the first, as «Primary · secondary»
                              // in meta type — never dropped (KAN-577; before,
                              // `compact` dropped it). Only `comfortable`
                              // stacks them.
                              // The primary is the column's fact and keeps
                              // priority (KAN-573; Anto, 2026-11-07, option A):
                              // it takes its own width up to the whole cell and
                              // never shrinks while the secondary has width
                              // left; the secondary — its «·» included —
                              // shrinks to an ellipsis, down to nothing, before
                              // a single character of the primary is cut.
                              <InlineComposite secondary={meta.secondary(row.original)}>
                                {flexRender(cell.column.columnDef.cell, cell.getContext())}
                              </InlineComposite>
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
            : L.showingOfTotal(rows.length, `${formatNumber(total)}${totalIsEstimate ? "+" : ""}`)}
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
  stickyLeft,
  align,
  width,
  minWidth,
  onMeasureHeading,
  onMeasureWidth,
  onResize,
}: {
  label: ReactNode;
  sortField?: string;
  sort: SortSpec[];
  onSortChange: (sort: SortSpec[]) => void;
  pinned?: "left" | "right";
  /** A left-pinned column's sticky offset: the widths of the left-pinned
   * columns before it (KAN-582). */
  stickyLeft?: number;
  align?: "left" | "right";
  width?: number;
  /** An unsized column's minimum (KAN-578): at least its heading's width. */
  minWidth?: number;
  /** Reports the width the label and sort indicator need (KAN-578). */
  onMeasureHeading?: (width: number) => void;
  /** Reports the cell's rendered width — an unsized left-pinned column's
   * share of the next pinned column's offset (KAN-582). */
  onMeasureWidth?: (width: number) => void;
  /** Present only when the caller wired `columnLayout` +
   * `onColumnLayoutChange` — renders the resize handle at all only then,
   * since without it there's nowhere to persist the result. */
  onResize?: (width: number) => void;
}) {
  const headingRef = useRef<HTMLSpanElement>(null);
  const cellRef = useRef<HTMLDivElement>(null);
  const measuresWidth = onMeasureWidth !== undefined;
  const measureWidthRef = useRef(onMeasureWidth);
  measureWidthRef.current = onMeasureWidth;
  useLayoutEffect(() => {
    const element = cellRef.current;
    if (!element || !measuresWidth) return;
    const measure = () => measureWidthRef.current?.(element.getBoundingClientRect().width);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [measuresWidth]);
  const sortIndex = sortField ? sort.findIndex((s) => s.field === sortField) : -1;
  // Measured when what the heading shows changes — its label (a language
  // switch), the sort indicator and its ordinal — never on a scroll; the grid
  // stores a width only when it changed, so this never loops.
  const ordinal = sort.length > 1 && sortIndex !== -1 ? sortIndex + 1 : 0;
  useLayoutEffect(() => {
    const heading = headingRef.current;
    if (heading && onMeasureHeading) onMeasureHeading(heading.scrollWidth);
    // onMeasureHeading is a new closure on every grid render; the width it
    // reports depends only on what the heading shows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label, sortIndex, ordinal]);
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
      ref={cellRef}
      role="columnheader"
      aria-sort={ariaSort}
      style={{
        position: pinned ? "sticky" : "relative",
        left: pinned === "left" ? (stickyLeft ?? 0) : undefined,
        right: pinned === "right" ? 0 : undefined,
        zIndex: pinned ? 3 : undefined,
        width: width ?? "1%",
        flex: width ? `0 0 ${width}px` : "1 1 0",
        minWidth: width ?? minWidth ?? UNSIZED_MIN_WIDTH,
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
      <span ref={headingRef} data-heading-label="" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
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
      {onResize && <ResizeHandle onResizeEnd={onResize} />}
    </div>
  );
}

function ResizeHandle({ onResizeEnd }: { onResizeEnd: (width: number) => void }) {
  const onMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation(); // never trigger the header's own sort click
    const headerEl = event.currentTarget.parentElement;
    const startWidth = headerEl?.getBoundingClientRect().width ?? 120;
    const startX = event.clientX;
    let currentWidth = startWidth;

    const onMouseMove = (moveEvent: MouseEvent) => {
      currentWidth = Math.max(60, startWidth + (moveEvent.clientX - startX));
    };
    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      onResizeEnd(currentWidth);
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  return (
    <div
      className="dg-resize-handle"
      aria-hidden="true"
      onMouseDown={onMouseDown}
      onClick={(e) => e.stopPropagation()} // a resize drag's own trailing click must not also toggle sort
    />
  );
}

function Row({
  children,
  style,
  href,
  Link,
  onActivate,
}: {
  children: ReactNode;
  style: CSSProperties;
  href?: string;
  Link?: LinkLike;
  onActivate?: () => void;
}) {
  const rowStyle: CSSProperties = {
    ...style,
    display: "flex",
    alignItems: "center",
    borderBottom: `1px solid ${slate[1]}`,
    ...(onActivate && !href ? { cursor: "pointer" } : {}),
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
  if (onActivate) {
    return (
      <div
        role="row"
        style={rowStyle}
        className="dg-row"
        tabIndex={0}
        onClick={onActivate}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate();
          }
        }}
      >
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
  label,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  label: string;
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
      <input type="checkbox" checked={checked} ref={inputRef} onChange={onChange} aria-label={label} />
    </div>
  );
}

/** The body's selection cell — laid out like `SelectHeaderCell`: centred,
 * no padding, no ellipsis (KAN-591). The generic `Cell` pads
 * `spacing.sm spacing.md` and clips with `text-overflow: ellipsis`; in the
 * 40 px column the padding plus the checkbox overflowed it, and the browser
 * drew a stray «…» beside every row's checkbox. */
function SelectCell({ children }: { children: ReactNode }) {
  return (
    <div
      role="cell"
      style={{
        position: "sticky",
        left: 0,
        backgroundColor: white,
        width: SELECTION_COLUMN_WIDTH,
        flex: `0 0 ${SELECTION_COLUMN_WIDTH}px`,
        minWidth: SELECTION_COLUMN_WIDTH,
        // The body row centres its cells (`alignItems: center`); the header
        // row stretches them. Stretched, the sticky white column covers the
        // row's whole height, as the padded Cell did, so content scrolled
        // under it sideways never shows above or below the checkbox.
        alignSelf: "stretch",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {children}
    </div>
  );
}

function SelectRowCheckbox({ checked, label, onChange }: { checked: boolean; label: string; onChange: () => void }) {
  // Sits in the sticky SelectCell (`position: sticky`), which already paints above
  // `.dg-row-link` — see Row's own comment. `stopPropagation` is a
  // defensive backstop, not what makes this clickable.
  return <input type="checkbox" checked={checked} onChange={onChange} aria-label={label} onClick={(e) => e.stopPropagation()} />;
}

/** § Composite cells, one line (`compact`, `default`): «Primary · secondary».
 * A row without a secondary fact shows the primary alone, with no stray «·». */
function InlineComposite({ children, secondary }: { children: ReactNode; secondary: ReactNode }) {
  const hasSecondary = secondary !== null && secondary !== undefined && secondary !== "" && secondary !== false;
  return (
    <div data-composite="inline" style={{ display: "flex", alignItems: "baseline", gap: spacing.xs, minWidth: 0 }}>
      <span data-composite-part="primary" style={INLINE_COMPOSITE_PRIMARY_STYLE}>
        {children}
      </span>
      {hasSecondary && (
        <span data-composite-part="secondary" style={INLINE_COMPOSITE_SECONDARY_STYLE}>
          {/* The «·» is presentation: a screen reader reads the two facts, not the dot. */}
          <span aria-hidden="true">{"· "}</span>
          {secondary}
        </span>
      )}
    </div>
  );
}

function Cell({
  children,
  pinned,
  stickyLeft,
  mono,
  align,
  width,
  minWidth,
}: {
  children: ReactNode;
  pinned?: "left" | "right";
  /** As the header's: after the left-pinned columns before it (KAN-582). */
  stickyLeft?: number;
  mono?: boolean;
  align?: "left" | "right";
  width?: number;
  /** An unsized column's minimum (KAN-578), the same as its header's. */
  minWidth?: number;
}) {
  return (
    <div
      role="cell"
      style={{
        position: pinned ? "sticky" : undefined,
        left: pinned === "left" ? (stickyLeft ?? 0) : undefined,
        right: pinned === "right" ? 0 : undefined,
        backgroundColor: pinned ? white : undefined,
        width: width ?? "1%",
        flex: width ? `0 0 ${width}px` : "1 1 0",
        minWidth: width ?? minWidth ?? UNSIZED_MIN_WIDTH,
        padding: `${spacing.sm} ${spacing.md}`,
        fontFamily: mono ? "ui-monospace, SF Mono, Menlo, monospace" : undefined,
        textAlign: align ?? "left",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {/* KAN-710: content that shows more than its one line (an inline
          edit's refusal) shows it outside the clipping cell. */}
      <GridCellContext.Provider value={true}>{children}</GridCellContext.Provider>
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

