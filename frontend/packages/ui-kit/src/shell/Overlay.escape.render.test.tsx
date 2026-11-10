// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider, Modal, Popover, Select } from "@mantine/core";
import { useEffect, useState, type ReactNode } from "react";
import { testTheme } from "../test/testTheme";
import { FR_ACTION_BAR_LABELS, FR_VIEWS_AND_FILTERS_LABELS } from "../test/frenchLabels";
import { ActionBar } from "../datagrid/ActionBar";
import { ColumnConfigPanel } from "../datagrid/ColumnConfigPanel";
import { ViewsAndFilters } from "../datagrid/ViewsAndFilters";
import { OverlayProvider, useOverlay } from "./Overlay";

// KAN-605 — the overlay popped its top layer on every Escape. Mantine's
// Popover, Combobox and Modal close themselves on Escape but never stop the
// event, so dismissing a filter panel, a dropdown or a dialog inside the
// configurator closed the configurator too and discarded its state
// (ADR-059). Escape now closes the innermost open thing; with nothing open
// inside, it closes the top layer as before.

function OpensOnMount({ content, onClose }: { content: ReactNode; onClose: () => void }) {
  const overlay = useOverlay();
  useEffect(() => {
    overlay.push({ key: "layer", content, onClose });
    // Push once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function mount(content: ReactNode) {
  const onClose = vi.fn();
  render(
    <MantineProvider theme={testTheme}>
      <OverlayProvider closeLabel="Fermer">
        <OpensOnMount content={content} onClose={onClose} />
      </OverlayProvider>
    </MantineProvider>,
  );
  return onClose;
}

const layerOpen = () => screen.queryByRole("button", { name: "Fermer" }) !== null;

function ModalInside() {
  const [opened, setOpened] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpened(true)}>
        Ouvrir le dialogue
      </button>
      <Modal opened={opened} onClose={() => setOpened(false)} title="Dialogue">
        <input aria-label="Champ du dialogue" />
      </Modal>
    </>
  );
}

describe("Overlay — Escape closes the innermost open thing (KAN-605)", () => {
  it("with nothing open inside, Escape closes the top layer and fires its onClose once", async () => {
    const user = userEvent.setup();
    const onClose = mount(<input aria-label="Champ" />);
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("textbox", { name: "Champ" }));
    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("an open Select dropdown: the first Escape closes the dropdown only, the second the layer", async () => {
    const user = userEvent.setup();
    const onClose = mount(<Select aria-label="Carburant" data={["Essence", "Diesel"]} />);
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("textbox", { name: "Carburant" }));
    expect(await screen.findByRole("option", { name: "Diesel" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("option", { name: "Diesel" })).toBeNull());
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  function viewsPanel() {
    return (
      <ViewsAndFilters
        currentViewName="Tous les véhicules"
        views={[]}
        onApplyView={() => {}}
        onSaveCurrentAsView={() => {}}
        onDeleteView={() => {}}
        onSetDefaultView={() => {}}
        fields={[{ id: "canton", label: "Canton", type: "text" }]}
        predicates={[]}
        onPredicatesChange={() => {}}
        onResetFilters={() => {}}
        labels={FR_VIEWS_AND_FILTERS_LABELS}
      />
    );
  }
  const panelOpen = () => screen.queryByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading) !== null;

  // Wherever focus is once the panel is open — on its trigger (Chrome
  // focuses a clicked button), nowhere (Safari and Firefox on macOS do not),
  // inside the panel, or on <body> after a click that unmounted the focused
  // control — the first Escape closes the panel and the layer stays.
  for (const [where, moveFocus] of [
    ["on its trigger", (trigger: HTMLElement) => trigger.focus()],
    ["on <body>", () => (document.activeElement as HTMLElement | null)?.blur()],
    ["inside the panel", () => (screen.getByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading).closest(".mantine-Popover-dropdown")!.querySelector("button") as HTMLElement).focus()],
  ] as const) {
    it(`an open views-and-filters panel, focus ${where}: Escape closes the panel only, the next Escape the layer`, async () => {
      const user = userEvent.setup();
      const onClose = mount(viewsPanel());
      await screen.findByRole("button", { name: "Fermer" });
      const trigger = screen.getByRole("button", { name: /Tous les véhicules/ });
      await user.click(trigger);
      await screen.findByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading);
      moveFocus(trigger);

      await user.keyboard("{Escape}");
      await waitFor(() => expect(panelOpen()).toBe(false));
      expect(layerOpen()).toBe(true);
      expect(onClose).not.toHaveBeenCalled();

      await user.keyboard("{Escape}");
      expect(layerOpen()).toBe(false);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  }

  it("after «Nouveau filtre…» (its button unmounts on click, focus falls to <body>): Escape closes the panel, never the layer", async () => {
    const user = userEvent.setup();
    const onClose = mount(viewsPanel());
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("button", { name: /Tous les véhicules/ }));
    await user.click(await screen.findByRole("button", { name: FR_VIEWS_AND_FILTERS_LABELS.filters.addFilter }));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(panelOpen()).toBe(false));
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the Columns panel: Escape closes it only, focus anywhere", async () => {
    const user = userEvent.setup();
    const onClose = mount(
      <ColumnConfigPanel
        registry={[{ id: "vin", label: "NIV", defaultVisible: true }]}
        layout={{ order: ["vin"], hidden: [], widths: {}, pinnedLeft: [] }}
        onLayoutChange={() => {}}
        labels={{
          trigger: "Colonnes",
          search: "Rechercher une colonne",
          reset: "Réinitialiser",
          presets: "Préréglages",
          lockedTooltip: "Verrouillée",
          pin: "Épingler",
          unpin: "Désépingler",
          moveUp: (c) => `Monter ${c}`,
          moveDown: (c) => `Descendre ${c}`,
        }}
      />,
    );
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("button", { name: "Colonnes" }));
    expect(await screen.findByPlaceholderText("Rechercher une colonne")).toBeInTheDocument();
    (document.activeElement as HTMLElement | null)?.blur();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByPlaceholderText("Rechercher une colonne")).toBeNull());
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a popover rendered in place inside the layer: Escape inside it closes the popover only", async () => {
    const user = userEvent.setup();
    function InPlace() {
      const [opened, setOpened] = useState(false);
      return (
        <Popover opened={opened} onChange={setOpened} withinPortal={false}>
          <Popover.Target>
            <button type="button" onClick={() => setOpened((o) => !o)}>
              Options
            </button>
          </Popover.Target>
          <Popover.Dropdown>
            <button type="button">Option A</button>
          </Popover.Dropdown>
        </Popover>
      );
    }
    const onClose = mount(<InPlace />);
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("button", { name: "Options" }));
    (await screen.findByRole("button", { name: "Option A" })).focus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Option A" })).toBeNull());
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a typed search in a grid's Action Bar: Escape clears it, the next Escape closes the layer", async () => {
    const user = userEvent.setup();
    function SearchBar() {
      const [search, setSearch] = useState("");
      return (
        <ActionBar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="Rechercher…"
          density="default"
          onDensityChange={() => {}}
          onRefresh={() => {}}
          labels={FR_ACTION_BAR_LABELS}
        />
      );
    }
    const onClose = mount(<SearchBar />);
    await screen.findByRole("button", { name: "Fermer" });
    const search = screen.getByPlaceholderText("Rechercher…");
    await user.type(search, "Golf");

    await user.keyboard("{Escape}");
    expect(search).toHaveValue("");
    expect(layerOpen()).toBe(true);

    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("content that handles Escape itself claims it with preventDefault (an inline edit's cancel): the layer stays", async () => {
    const user = userEvent.setup();
    const onClose = mount(
      <input
        aria-label="Édition"
        onKeyDown={(e) => {
          if (e.key === "Escape") e.preventDefault();
        }}
      />,
    );
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("textbox", { name: "Édition" }));
    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("an expanded disclosure is not a popup: Escape on its toggle closes the layer", async () => {
    const user = userEvent.setup();
    const onClose = mount(
      <button type="button" aria-expanded="true">
        Détails
      </button>,
    );
    await screen.findByRole("button", { name: "Fermer" });
    screen.getByRole("button", { name: "Détails" }).focus();
    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a modal opened inside the layer: Escape closes the modal only", async () => {
    const user = userEvent.setup();
    const onClose = mount(<ModalInside />);
    await screen.findByRole("button", { name: "Fermer" });
    await user.click(screen.getByRole("button", { name: "Ouvrir le dialogue" }));
    await user.click(await screen.findByRole("textbox", { name: "Champ du dialogue" }));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Champ du dialogue" })).toBeNull());
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
  });
});
