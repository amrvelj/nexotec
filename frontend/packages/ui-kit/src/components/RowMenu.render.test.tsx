// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { DataGrid } from "../datagrid/DataGrid";
import type { GridColumnDef } from "../datagrid/types";
import { RowMenu } from "./RowMenu";

// jsdom has no layout — render every row (same mock as the other grid tests).
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize();
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    };
  },
}));

// KAN-252 — Mantine's Menu.Target opens the menu by cloning its child with
// an onClick and a ref. The default `⋯` trigger every grid row renders used
// to drop both, so no row menu ever opened.

const wrap = (ui: ReactNode) => render(<MantineProvider>{ui}</MantineProvider>);

interface Row {
  id: string;
  name: string;
}

const columns: GridColumnDef<Row>[] = [{ id: "name", header: "Name", cell: ({ row }) => row.original.name }];

describe("RowMenu default trigger", () => {
  it("opens on click and runs the picked action", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    wrap(<RowMenu ariaLabel="Zeilenaktionen" groups={{ navigate: [{ label: "Öffnen", icon: null, onClick: onOpen }] }} />);

    await user.click(screen.getByRole("button", { name: "Zeilenaktionen" }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Öffnen" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("opens from the keyboard", async () => {
    const user = userEvent.setup();
    wrap(<RowMenu ariaLabel="Zeilenaktionen" groups={{ edit: [{ label: "Bearbeiten", icon: null, onClick: () => {} }] }} />);

    screen.getByRole("button", { name: "Zeilenaktionen" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("menu")).toBeInTheDocument();
  });

  const gridWithRowMenu = (onRowActivate: () => void, onEdit: () => void) => (
    <DataGrid<Row>
      columns={columns}
      rows={[{ id: "a", name: "Alfa" }]}
      getRowId={(r) => r.id}
      sort={[]}
      onSortChange={() => {}}
      density="default"
      loading={false}
      fetchingNextPage={false}
      hasNextPage={false}
      onLoadMore={() => {}}
      total={1}
      totalIsEstimate={false}
      isFiltered={false}
      onRowActivate={onRowActivate}
      rowActions={() => ({ edit: [{ label: "Bearbeiten", icon: null, onClick: onEdit }] })}
      labels={{ rowActionsLabel: "Zeilenaktionen" }}
      emptyState={{ icon: null, title: "", description: "" }}
    />
  );

  it("opens a grid row's menu on click without activating the row", async () => {
    const user = userEvent.setup();
    const onRowActivate = vi.fn();
    const onEdit = vi.fn();
    wrap(gridWithRowMenu(onRowActivate, onEdit));

    await user.click(screen.getByRole("button", { name: "Zeilenaktionen" }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(onRowActivate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("menuitem", { name: "Bearbeiten" }));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onRowActivate).not.toHaveBeenCalled();
  });

  it("opens a grid row's menu and runs an item on Enter, without activating the row", async () => {
    const user = userEvent.setup();
    const onRowActivate = vi.fn();
    const onEdit = vi.fn();
    wrap(gridWithRowMenu(onRowActivate, onEdit));

    screen.getByRole("button", { name: "Zeilenaktionen" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(onRowActivate).not.toHaveBeenCalled();

    screen.getByRole("menuitem", { name: "Bearbeiten" }).focus();
    await user.keyboard("{Enter}");
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onRowActivate).not.toHaveBeenCalled();
  });
});
