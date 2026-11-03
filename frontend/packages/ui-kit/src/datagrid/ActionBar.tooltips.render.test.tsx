// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { ActionBar } from "./ActionBar";
import { FR_ACTION_BAR_LABELS } from "../test/frenchLabels";
import { testTheme } from "../test/testTheme";

// KAN-420 — the density and refresh buttons sit in Mantine Tooltips, which
// clone their child with a ref and mouse/focus handlers. The bar's
// IconButton dropped both, so neither tooltip ever opened; the density
// tooltip is the only place that names the current density.

function renderBar(density: "compact" | "default" | "comfortable" = "default") {
  return render(
    <MantineProvider theme={testTheme}>
      <ActionBar
        searchValue=""
        onSearchChange={() => {}}
        searchPlaceholder="Rechercher des véhicules…"
        density={density}
        onDensityChange={() => {}}
        onRefresh={() => {}}
        labels={FR_ACTION_BAR_LABELS}
      />
    </MantineProvider>,
  );
}

describe("ActionBar tooltips (KAN-420)", () => {
  it("hovering the density button shows the current density's name", async () => {
    const user = userEvent.setup();
    renderBar("comfortable");

    await user.hover(screen.getByRole("button", { name: "Changer la densité des lignes" }));

    expect(await screen.findByRole("tooltip")).toHaveTextContent("Densité : Confortable");
  });

  it("focusing the density button from the keyboard shows it too", async () => {
    const user = userEvent.setup();
    renderBar("compact");

    // Search is focused on load; Tab moves to the density button.
    await user.tab();

    expect(screen.getByRole("button", { name: "Changer la densité des lignes" })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Densité : Compacte");
  });

  it("hovering the refresh button shows its own tooltip", async () => {
    const user = userEvent.setup();
    renderBar();

    await user.hover(screen.getByRole("button", { name: "Actualiser" }));

    expect(await screen.findByRole("tooltip")).toHaveTextContent("Actualiser");
  });

  it("the buttons keep their hover colour", async () => {
    const user = userEvent.setup();
    renderBar();
    const button = screen.getByRole("button", { name: "Actualiser" });

    await user.hover(button);
    expect(button.style.backgroundColor).not.toBe("transparent");
    expect(button.style.backgroundColor).not.toBe("");
    await user.unhover(button);
    expect(button.style.backgroundColor).toBe("transparent");
  });
});
