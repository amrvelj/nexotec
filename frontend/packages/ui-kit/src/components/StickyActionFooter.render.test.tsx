// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider, NumberInput } from "@mantine/core";
import { StickyActionFooter } from "./StickyActionFooter";
import { zIndex } from "../tokens";

// KAN-305 — the configurator's number fields scrolled over the sticky save
// footer: Mantine's NumberInput spinners are positioned elements and the
// footer had no stacking level, so they were painted on top of "Konfiguration
// speichern". jsdom has no layout or painting, so this pins the styles that
// make the browser stack the footer above its siblings; the PR carries the
// screenshot.

describe("StickyActionFooter — stacks above the content scrolling under it (KAN-305)", () => {
  it("is sticky at the bottom with its own stacking level from the tokens", () => {
    render(
      <MantineProvider>
        <NumberInput label="Leergewicht" defaultValue={1500} />
        <StickyActionFooter missingLabel="Es fehlt noch: Kunde" primaryAction={<button type="button">Speichern</button>} />
      </MantineProvider>,
    );

    const footer = screen.getByRole("button", { name: "Speichern" }).parentElement as HTMLElement;
    expect(footer.style.position).toBe("sticky");
    expect(footer.style.bottom).toBe("0px");
    expect(footer.style.zIndex).toBe(String(zIndex.stickyBar));
    expect(zIndex.stickyBar).toBeGreaterThan(0);
  });
});

// KAN-368 (ADR-061) — a disabled primary action says why on hover, as
// DetailHeader's disabled actions do; without a reason nothing wraps it.
describe("StickyActionFooter — a disabled primary action says why (KAN-368)", () => {
  it("shows the reason when the disabled action is hovered", async () => {
    render(
      <MantineProvider>
        <StickyActionFooter
          primaryAction={<button type="button" disabled>Weiter</button>}
          primaryDisabledReason="Änderungen werden noch gespeichert."
        />
      </MantineProvider>,
    );

    await userEvent.hover(screen.getByRole("button", { name: "Weiter" }).parentElement as HTMLElement);

    expect(await screen.findByText("Änderungen werden noch gespeichert.")).toBeInTheDocument();
  });

  it("keeps the same action element while the reason comes and goes", () => {
    const footer = (reason: string | null) => (
      <MantineProvider>
        <StickyActionFooter primaryAction={<button type="button" disabled={!!reason}>Weiter</button>} primaryDisabledReason={reason} />
      </MantineProvider>
    );
    const { rerender } = render(footer("Änderungen werden noch gespeichert."));
    const before = screen.getByRole("button", { name: "Weiter" });

    rerender(footer(null));

    expect(screen.getByRole("button", { name: "Weiter" })).toBe(before);
    expect(before).toBeEnabled();
  });

  it("renders the action unwrapped when the caller passes no reason", () => {
    render(
      <MantineProvider>
        <StickyActionFooter primaryAction={<button type="button">Weiter</button>} />
      </MantineProvider>,
    );

    expect((screen.getByRole("button", { name: "Weiter" }).parentElement as HTMLElement).style.position).toBe("sticky");
  });
});
