// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { DataGrid } from "../datagrid/DataGrid";
import type { GridColumnDef } from "../datagrid/types";
import { FR_DATA_GRID_LABELS } from "../test/frenchLabels";
import { testTheme } from "../test/testTheme";
import { InlineEditField } from "./InlineEditField";

// jsdom has no layout (see DataGrid.rowActivate.render.test.tsx): render
// every row instead of letting TanStack Virtual window down to none.
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

// KAN-710 — a DataGrid cell is a fixed-height box that clips (overflow
// hidden, one line). An InlineEditField in a cell (Reference data's language
// columns, the customer's External IDs) put its refused save and «reload»
// below the editor, inside the cell: cut off, so a refused save looked like
// one that silently did nothing. Inside a grid cell the refusal now opens in
// a popover in a portal — outside the clipping cell — and «reload» stays
// reachable by mouse and keyboard without saving the refused value again.

class StaleVersion extends Error {}

interface Row {
  id: string;
  label: string;
}

function renderGrid(opts: { onSave: (raw: string) => Promise<void>; onReload: () => void }) {
  const columns: GridColumnDef<Row>[] = [
    {
      id: "label",
      header: "Libellé",
      cell: ({ row }) => (
        <InlineEditField
          value={row.original.label}
          emptyLabel="Non renseigné"
          onSave={opts.onSave}
          describeError={(e) => (e instanceof StaleVersion ? "Cet enregistrement a été modifié entre-temps." : "Refusé.")}
          isConflict={(e) => e instanceof StaleVersion}
          onReload={opts.onReload}
          reloadLabel="Recharger"
        />
      ),
    },
  ];
  return render(
    <MantineProvider theme={testTheme}>
      <DataGrid<Row>
        columns={columns}
        rows={[{ id: "petrol", label: "Benzin" }]}
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
        labels={FR_DATA_GRID_LABELS}
        emptyState={{ icon: null, title: "", description: "" }}
      />
      {/* A page always goes on after the grid; without this the browser's
          own Shift+Tab from the portal at the end would land on the editor
          and the test could not fail (re-review finding A). */}
      <button type="button">Après la grille</button>
    </MantineProvider>,
  );
}

async function refuseAnEdit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText("Benzin"));
  const input = screen.getByDisplayValue("Benzin");
  await user.type(input, " x{Enter}");
  return input;
}

function theCell(): HTMLElement {
  return screen.getAllByRole("cell").find((cell) => cell.querySelector("input")) as HTMLElement;
}

describe("InlineEditField inside a DataGrid cell (KAN-710)", () => {
  it("shows the refusal and «reload» outside the clipping cell", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => {
      throw new StaleVersion();
    });
    renderGrid({ onSave, onReload: vi.fn() });

    await refuseAnEdit(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cet enregistrement a été modifié entre-temps.");
    expect(within(alert).getByRole("button", { name: "Recharger" })).toBeInTheDocument();
    // Not inside the cell, which clips: the popover sits in a portal.
    expect(theCell().contains(alert)).toBe(false);
    expect(within(theCell()).queryByText("Cet enregistrement a été modifié entre-temps.")).toBeNull();
  });

  it("«reload» by mouse reloads without saving the refused value again", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => {
      throw new StaleVersion();
    });
    const onReload = vi.fn();
    renderGrid({ onSave, onReload });

    const input = await refuseAnEdit(user);
    await user.click(await screen.findByRole("button", { name: "Recharger" }));

    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(input).toHaveFocus();
  });

  it("«reload» by keyboard: Tab from the editor reaches it, Enter presses it, no second save", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => {
      throw new StaleVersion();
    });
    const onReload = vi.fn();
    renderGrid({ onSave, onReload });

    await refuseAnEdit(user);
    const reload = await screen.findByRole("button", { name: "Recharger" });
    await user.tab();
    expect(reload).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("Shift+Tab from «reload» returns to the editor without saving again", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn(async () => {
      throw new StaleVersion();
    });
    renderGrid({ onSave, onReload: vi.fn() });

    const input = await refuseAnEdit(user);
    const reload = await screen.findByRole("button", { name: "Recharger" });
    await user.tab();
    expect(reload).toHaveFocus();
    await user.tab({ shift: true });

    expect(input).toHaveFocus();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("names the popover by its refusal", async () => {
    const user = userEvent.setup();
    renderGrid({
      onSave: async () => {
        throw new StaleVersion();
      },
      onReload: vi.fn(),
    });

    await refuseAnEdit(user);

    expect(await screen.findByRole("dialog", { name: "Cet enregistrement a été modifié entre-temps." })).toBeInTheDocument();
  });

  it("Tab between the fields of a compound editor stays the browser's; from the last one it reaches «reload»", async () => {
    const user = userEvent.setup();
    const columns: GridColumnDef<Row>[] = [
      {
        id: "label",
        header: "Libellé",
        cell: ({ row }) => (
          <InlineEditField
            value={row.original.label}
            emptyLabel="Non renseigné"
            onSave={async () => {
              throw new StaleVersion();
            }}
            describeError={() => "Cet enregistrement a été modifié entre-temps."}
            isConflict={() => true}
            onReload={vi.fn()}
            reloadLabel="Recharger"
            renderEditor={({ value, onChange }) => (
              <span>
                <input aria-label="premier" value={value} onChange={(e) => onChange(e.currentTarget.value)} autoFocus />
                <input aria-label="second" defaultValue="" />
                {/* As Mantine's Select renders its value: never a tab stop. */}
                <input type="hidden" name="valeur" value={value} readOnly />
              </span>
            )}
          />
        ),
      },
    ];
    render(
      <MantineProvider theme={testTheme}>
        <DataGrid<Row>
          columns={columns}
          rows={[{ id: "petrol", label: "Benzin" }]}
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
          labels={FR_DATA_GRID_LABELS}
          emptyState={{ icon: null, title: "", description: "" }}
        />
        <button type="button">Après la grille</button>
      </MantineProvider>,
    );

    await user.click(screen.getByText("Benzin"));
    await user.type(screen.getByLabelText("premier"), " x{Enter}");
    const reload = await screen.findByRole("button", { name: "Recharger" });
    screen.getByLabelText("premier").focus();

    await user.tab();
    expect(screen.getByLabelText("second")).toHaveFocus();
    await user.tab();
    expect(reload).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByLabelText("second")).toHaveFocus();
  });

  it("outside a grid the refusal stays inline below the editor, as before", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider theme={testTheme}>
        <div data-testid="host">
          <InlineEditField
            value="Benzin"
            emptyLabel="Non renseigné"
            onSave={async () => {
              throw new StaleVersion();
            }}
            describeError={() => "Refusé."}
          />
        </div>
      </MantineProvider>,
    );

    await refuseAnEdit(user);

    const alert = await screen.findByRole("alert");
    expect(screen.getByTestId("host").contains(alert)).toBe(true);
  });
});
