// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Topbar } from "./Topbar";

// KAN-301 — at 1024-1280 px the record name wrapped onto two lines ("Marco" /
// "Rossi") and slid under the centred search. jsdom has no layout, so this
// pins the styles that keep the breadcrumb on one line and cut a segment with
// an ellipsis (full text in `title`), the record name giving way last; the PR
// carries the screenshots at 1024 and 1280 px.

function renderTopbar(segments: string[]) {
  return render(
    <MantineProvider>
      <Topbar breadcrumb={segments} />
    </MantineProvider>,
  );
}

describe("Topbar breadcrumb — one line, ellipsis, record name last (KAN-301)", () => {
  it("never wraps and lets its column shrink it", () => {
    const { container } = renderTopbar(["Stammdaten", "Kunden", "Marco Rossi"]);
    const crumb = container.querySelector<HTMLElement>("[data-breadcrumb]")!;
    expect(crumb.style.flexWrap).toBe("nowrap");
    expect(crumb.style.minWidth).toBe("0px");
    expect(crumb.style.overflow).toBe("hidden");
  });

  it("cuts each segment with an ellipsis and keeps the full text in its title", () => {
    renderTopbar(["Stammdaten", "Kunden", "Marco Rossi"]);
    for (const text of ["Stammdaten", "Kunden", "Marco Rossi"]) {
      const label = screen.getByText(text);
      expect(label.getAttribute("title")).toBe(text);
      expect(label.style.whiteSpace).toBe("nowrap");
      expect(label.style.textOverflow).toBe("ellipsis");
      expect(label.style.overflow).toBe("hidden");
    }
  });

  it("shrinks the leading segments before the record name", () => {
    const { container } = renderTopbar(["Stammdaten", "Kunden", "Marco Rossi"]);
    const [first, second, last] = Array.from(container.querySelectorAll<HTMLElement>("[data-breadcrumb-segment]"));
    expect(Number(first.style.flexShrink)).toBeGreaterThan(Number(last.style.flexShrink));
    expect(Number(second.style.flexShrink)).toBeGreaterThan(Number(last.style.flexShrink));
  });
});
