// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Topbar } from "./Topbar";
import { testTheme } from "../test/testTheme";

// KAN-301 — at 1024-1280 px the record name wrapped onto two lines ("Marco" /
// "Rossi") and slid under the centred search. jsdom has no layout, so this
// pins the styles that keep the breadcrumb on one line and cut a segment with
// an ellipsis (full text in `title`), the record name giving way last; the PR
// carries the screenshots at 1024 and 1280 px.

function renderTopbar(segments: string[]) {
  return render(
    <MantineProvider theme={testTheme}>
      <Topbar breadcrumb={segments} />
    </MantineProvider>,
  );
}

describe("Topbar breadcrumb — one line, ellipsis, record name last (KAN-301)", () => {
  it("never wraps and lets its column shrink it", () => {
    const { container } = renderTopbar(["Stammdaten", "Kontakte", "Marco Rossi"]);
    const crumb = container.querySelector<HTMLElement>("[data-breadcrumb]")!;
    expect(crumb.style.flexWrap).toBe("nowrap");
    expect(crumb.style.minWidth).toBe("0px");
    expect(crumb.style.overflow).toBe("hidden");
  });

  it("cuts each segment with an ellipsis and keeps the full text in its title", () => {
    renderTopbar(["Stammdaten", "Kontakte", "Marco Rossi"]);
    for (const text of ["Stammdaten", "Kontakte", "Marco Rossi"]) {
      const label = screen.getByText(text);
      expect(label.getAttribute("title")).toBe(text);
      expect(label.style.whiteSpace).toBe("nowrap");
      expect(label.style.textOverflow).toBe("ellipsis");
      expect(label.style.overflow).toBe("hidden");
    }
  });

  it("shrinks the leading segments before the record name", () => {
    const { container } = renderTopbar(["Stammdaten", "Kontakte", "Marco Rossi"]);
    const [first, second, last] = Array.from(container.querySelectorAll<HTMLElement>("[data-breadcrumb-segment]"));
    expect(Number(first.style.flexShrink)).toBeGreaterThan(Number(last.style.flexShrink));
    expect(Number(second.style.flexShrink)).toBeGreaterThan(Number(last.style.flexShrink));
  });
});

// KAN-104 item 1 (FR-UI-07): the dashboard is the breadcrumb's root, and a
// segment that names a screen is a link. The labels are the app's.
describe("Topbar breadcrumb — the dashboard is the root, screens are links (KAN-104)", () => {
  const root = { label: "Übersicht", href: "/" };

  function renderWithRoot(segments: Parameters<typeof Topbar>[0]["breadcrumb"]) {
    return render(
      <MantineProvider theme={testTheme}>
        <Topbar breadcrumb={segments} breadcrumbRoot={root} />
      </MantineProvider>,
    );
  }

  it("puts the root first, as a link", () => {
    renderWithRoot(["Stammdaten", { label: "Kontakte", href: "/contacts" }, "Marco Rossi"]);
    const segments = Array.from(document.querySelectorAll<HTMLElement>("[data-breadcrumb-segment]")).map((s) => s.textContent);
    expect(segments).toEqual(["Übersicht", "/Stammdaten", "/Kontakte", "/Marco Rossi"]);
    expect(screen.getByRole("link", { name: "Übersicht" })).toHaveAttribute("href", "/");
  });

  it("links a segment that names a screen, never a plain group or the current screen", () => {
    renderWithRoot(["Stammdaten", { label: "Kontakte", href: "/contacts" }, { label: "Marco Rossi", href: "/contacts/1" }]);
    expect(screen.getByRole("link", { name: "Kontakte" })).toHaveAttribute("href", "/contacts");
    expect(screen.queryByRole("link", { name: "Stammdaten" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Marco Rossi" })).toBeNull();
    expect(screen.getByText("Marco Rossi")).toHaveAttribute("aria-current", "page");
  });

  it("names the landing page once, not twice", () => {
    renderWithRoot(["Übersicht"]);
    expect(screen.getAllByText("Übersicht")).toHaveLength(1);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("draws a root with an icon as the icon alone, named by its label, never shrunk", () => {
    render(
      <MantineProvider theme={testTheme}>
        <Topbar breadcrumb={["Stammdaten", "Kontakte"]} breadcrumbRoot={{ ...root, icon: <svg data-testid="root-icon" /> }} />
      </MantineProvider>,
    );
    const link = screen.getByRole("link", { name: "Übersicht" });
    expect(link).toHaveAttribute("href", "/");
    expect(screen.getByTestId("root-icon")).toBeInTheDocument();
    expect(screen.queryByText("Übersicht")).toBeNull(); // no text beside the icon
    const first = document.querySelector<HTMLElement>("[data-breadcrumb-segment]")!;
    expect(first.style.flexShrink).toBe("0");
  });

  it("keeps a segment's ellipsis and full text when it is a link", () => {
    renderWithRoot([{ label: "Kontakte", href: "/contacts" }, "Marco Rossi"]);
    const label = screen.getByText("Kontakte");
    expect(label.getAttribute("title")).toBe("Kontakte");
    const link = screen.getByRole("link", { name: "Kontakte" });
    expect(link.style.textOverflow).toBe("ellipsis");
    expect(link.style.whiteSpace).toBe("nowrap");
  });
});
