import { useEffect, useRef, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronsUpDown, ChevronUp, CircleAlert, MoreHorizontal } from "lucide-react";
import { Menu } from "@mantine/core";
import { purple, radius, slate, slate25, spacing } from "../tokens";
import { cycleSort } from "./sorting";
import { ROW_HEIGHT, type Density, type EmptyStateConfig, type GridColumnDef, type SortSpec } from "./types";

type LinkLike = ComponentType<{ to: string; children?: ReactNode; style?: CSSProperties; className?: string }>;

const ACTION_COLUMN_WIDTH = 48;

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
  locale,
  labels,
}: DataGridProps<T>) {
  const L = { ...DEFAULT_GRID_LABELS, ...labels };
  const Link = linkComponent;
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowHeight = ROW_HEIGHT[density];

  const allColumns: GridColumnDef<T>[] = rowActions
    ? [
        ...columns,
        {
          id: "__actions",
          header: "",
          cell: () => null,
          meta: { pinned: "right" },
        },
      ]
    : columns;

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

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        border: `1px solid ${slate[2]}`,
        borderRadius: radius.lg,
        backgroundColor: "#fff",
        overflow: "hidden",
      }}
    >
      <div ref={scrollRef} style={{ overflow: "auto", maxHeight: "70vh" }}>
        {/* Virtualized rows are absolutely positioned, which real <table>
            elements can't host correctly (the browser's table layout
            algorithm fights it, and <div>/<a> aren't valid <tbody>
            children anyway) — a div grid with ARIA table roles keeps the
            same semantics/accessibility without that conflict, the
            standard pattern for a virtualized table. */}
        <div role="table" style={{ width: "100%" }}>
          <div role="rowgroup" style={{ position: "sticky", top: 0, zIndex: 2 }}>
            {table.getHeaderGroups().map((headerGroup) => (
              <div role="row" key={headerGroup.id} style={{ display: "flex" }}>
                {headerGroup.headers.map((header) => {
                  const meta = header.column.columnDef.meta as GridColumnDef<T>["meta"];
                  const isActions = header.column.id === "__actions";
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
                      }}
                      href={href}
                      Link={Link}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const meta = cell.column.columnDef.meta as GridColumnDef<T>["meta"];
                        const isActions = cell.column.id === "__actions";
                        return (
                          <Cell key={cell.id} pinned={isActions ? "right" : meta?.pinned} mono={meta?.mono} align={meta?.align} width={isActions ? ACTION_COLUMN_WIDTH : undefined}>
                            {isActions && rowActions ? (
                              <RowActionsMenu ariaLabel={L.rowActionsLabel}>{rowActions(row.original)}</RowActionsMenu>
                            ) : (
                              <>
                                <div style={{ fontSize: 14, color: slate[9] }}>
                                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                </div>
                                {density !== "compact" && meta?.secondary && (
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
              color: "#DC2626",
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
                  background: "#fff",
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

  return (
    <div
      role="columnheader"
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
    textDecoration: "none",
    color: "inherit",
  };
  if (href) {
    const Component = Link ?? "a";
    return (
      <Component to={href} href={href} role="row" style={rowStyle} className="dg-row">
        {children}
      </Component>
    );
  }
  return (
    <div role="row" style={rowStyle} className="dg-row">
      {children}
    </div>
  );
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
        backgroundColor: pinned ? "#fff" : undefined,
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
