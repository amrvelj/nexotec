// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider, Modal, Select } from "@mantine/core";
import { useEffect, useState, type ReactNode } from "react";
import { testTheme } from "../test/testTheme";
import { FR_ACTION_BAR_LABELS, FR_VIEWS_AND_FILTERS_LABELS } from "../test/frenchLabels";
import { ActionBar } from "../datagrid/ActionBar";
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

  it("an open views-and-filters panel: focus moves into it, Escape closes the panel and returns focus, the next Escape the layer", async () => {
    const user = userEvent.setup();
    const onClose = mount(
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
      />,
    );
    await screen.findByRole("button", { name: "Fermer" });
    const trigger = screen.getByRole("button", { name: /Tous les véhicules/ });
    await user.click(trigger);
    const panel = (await screen.findByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading)).closest(".mantine-Popover-dropdown") as HTMLElement;
    // Opening the panel moves focus into it — whether or not the browser
    // focused the trigger on click (Safari and Firefox on macOS do not).
    await waitFor(() => expect(panel.contains(document.activeElement)).toBe(true));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(FR_VIEWS_AND_FILTERS_LABELS.viewsHeading)).toBeNull());
    expect(layerOpen()).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(trigger));

    await user.keyboard("{Escape}");
    expect(layerOpen()).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
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
