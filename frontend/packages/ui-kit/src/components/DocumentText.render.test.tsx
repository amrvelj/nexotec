// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { DocumentText } from "./DocumentText";
import { testTheme } from "../test/testTheme";

// KAN-727 — the kit's document renderer: what counsel's ToS and DPA use
// (headings, notes, lists, emphasis, links), as React text, never as HTML.

const wrap = (ui: ReactNode) => render(<MantineProvider theme={testTheme}>{ui}</MantineProvider>);

describe("DocumentText", () => {
  it("renders a bulleted and a numbered list as lists, not as raw «- » lines", () => {
    const { container } = wrap(
      <DocumentText
        markdown={"## Pflichten\n\nDer Betrieb verpflichtet sich:\n- zur Sorgfalt\n- zur **Vertraulichkeit**\n\n1. Erstens\n2. Zweitens\n   mit Fortsetzung"}
      />,
    );
    expect(screen.getByRole("heading", { name: "Pflichten" })).toBeTruthy();
    const lists = container.querySelectorAll("ul, ol");
    expect(lists).toHaveLength(2);
    expect(lists[0].tagName).toBe("UL");
    expect(lists[1].tagName).toBe("OL");
    const bullets = Array.from(lists[0].querySelectorAll("li")).map((li) => li.textContent);
    expect(bullets).toEqual(["zur Sorgfalt", "zur Vertraulichkeit"]);
    expect(lists[0].querySelector("strong")?.textContent).toBe("Vertraulichkeit");
    expect(Array.from(lists[1].querySelectorAll("li")).map((li) => li.textContent)).toEqual([
      "Erstens",
      "Zweitens\nmit Fortsetzung",
    ]);
    expect(screen.getByText("Der Betrieb verpflichtet sich:")).toBeTruthy();
    expect(container.textContent).not.toContain("- zur");
  });

  it("renders headings, a note, emphasis and a safe link", () => {
    const { container } = wrap(
      <DocumentText
        markdown={"# Nutzungsbedingungen\n\n> Entwurf, nicht verbindlich.\n\nSiehe *Anhang* und [die Website](https://example.com/agb)."}
      />,
    );
    expect(screen.getByRole("heading", { name: "Nutzungsbedingungen" })).toBeTruthy();
    expect(screen.getByText("Entwurf, nicht verbindlich.")).toBeTruthy();
    expect(container.querySelector("em")?.textContent).toBe("Anhang");
    const link = screen.getByRole("link", { name: "die Website" });
    expect(link.getAttribute("href")).toBe("https://example.com/agb");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("never renders markup from the body, and an unsafe link target as its text alone", () => {
    const { container } = wrap(
      <DocumentText markdown={'<img src=x onerror="alert(1)"> and [klick](javascript:alert(1))'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(container.textContent).toContain("klick");
    expect(container.textContent).not.toContain("javascript:");
  });

  it("keeps the number a numbered list starts at, so clauses set apart by body text keep theirs", () => {
    const { container } = wrap(
      <DocumentText markdown={"3. Haftung\n\nDer Betrieb haftet nach Gesetz.\n\n4. Anwendbares Recht\n\n1. a\n\n2. b"} />,
    );
    const lists = Array.from(container.querySelectorAll("ol"));
    expect(lists.map((ol) => ol.getAttribute("start"))).toEqual(["3", "4", null, "2"]);
    expect(lists.map((ol) => ol.textContent)).toEqual(["Haftung", "Anwendbares Recht", "a", "b"]);
  });

  it("leaves an underscore inside a word as written, and a note's later lines may leave out the «>»", () => {
    const { container } = wrap(
      <DocumentText markdown={"Feld field_name_x und _betont_.\n\n> erste Zeile\nzweite Zeile"} />,
    );
    expect(container.textContent).toContain("field_name_x");
    expect(Array.from(container.querySelectorAll("em")).map((em) => em.textContent)).toEqual(["betont"]);
    const note = container.querySelector(".mantine-Blockquote-root");
    expect(note?.textContent).toBe("erste Zeile\nzweite Zeile");
    expect(container.textContent).not.toContain(">");
  });
});
