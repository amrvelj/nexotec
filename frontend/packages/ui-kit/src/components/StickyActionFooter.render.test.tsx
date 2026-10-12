// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
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
