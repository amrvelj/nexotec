// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { layLanes, ResourceDayGrid, type DayGridGroup } from "./ResourceDayGrid";
import { testTheme } from "../test/testTheme";

// KAN-809 — the kit's resource day grid: the caller's strings only, items as
// buttons in the tab order, overlapping items in lanes, «neu einplanen» as
// text and not by colour alone, a hatched span carrying its meaning.

const wrap = (ui: ReactNode) => render(<MantineProvider theme={testTheme}>{ui}</MantineProvider>);

const labels = {
  resource: "Ressource",
  unavailable: "nicht im Dienst",
  now: "Jetzt",
  hour: (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, "0")}:00`,
};

function groups(onActivate = vi.fn()): DayGridGroup[] {
  return [
    {
      id: "people",
      label: "Techniker",
      rows: [
        {
          id: "lea",
          title: "Lea Meier",
          subtitle: "Mechatronikerin",
          unavailable: [{ startMinute: 0, endMinute: 450 }],
          items: [
            { id: "b1", startMinute: 540, endMinute: 600, title: "Auftrag 1001", detail: "09:00–10:00", tone: "booking", ariaLabel: "Auftrag 1001, 09:00 bis 10:00", onActivate },
            { id: "b2", startMinute: 570, endMinute: 630, title: "Auftrag 1002", tone: "booking", flagLabel: "neu einplanen", ariaLabel: "Auftrag 1002", onActivate },
          ],
        },
      ],
    },
    {
      id: "places",
      label: "Arbeitsplätze",
      rows: [{ id: "lift", title: "Bühne 1", items: [{ id: "k1", startMinute: 480, endMinute: 720, title: "Gesperrt", tone: "block", ariaLabel: "Sperre", onActivate }] }],
    },
  ];
}

describe("ResourceDayGrid (KAN-809)", () => {
  it("lays overlapping items in lanes and lets touching ones share a lane", () => {
    const laid = layLanes([
      { id: "a", startMinute: 540, endMinute: 600 },
      { id: "b", startMinute: 570, endMinute: 630 },
      { id: "c", startMinute: 600, endMinute: 660 },
    ]);
    expect(laid.map(({ item, lane }) => [item.id, lane])).toEqual([["a", 0], ["b", 1], ["c", 0]]);
  });

  it("renders the caller's labels, the groups and rows, and every item as a button in order", () => {
    wrap(<ResourceDayGrid fromMinute={420} toMinute={1080} gridMinutes={15} groups={groups()} labels={labels} ariaLabel="Planer, Montag" />);
    const grid = screen.getByRole("group", { name: "Planer, Montag" });
    expect(within(grid).getByText("Ressource")).toBeInTheDocument();
    expect(within(grid).getByText("07:00")).toBeInTheDocument();
    expect(within(grid).getByText("17:00")).toBeInTheDocument();
    expect(within(grid).queryByText("18:00")).not.toBeInTheDocument();
    expect(within(grid).getByText("Techniker")).toBeInTheDocument();
    expect(within(grid).getByText("Mechatronikerin")).toBeInTheDocument();
    expect(within(grid).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Auftrag 1001, 09:00 bis 10:00",
      "Auftrag 1002",
      "Sperre",
    ]);
  });

  it("says «neu einplanen» in words, and a hatched span says what it means", () => {
    const { container } = wrap(<ResourceDayGrid fromMinute={420} toMinute={1080} groups={groups()} labels={labels} ariaLabel="Planer" />);
    expect(within(screen.getByRole("button", { name: "Auftrag 1002" })).getByText("neu einplanen")).toBeInTheDocument();
    const hatched = container.querySelector("[data-unavailable]");
    expect(hatched?.getAttribute("title")).toBe("nicht im Dienst");
  });

  it("activates an item by click and by keyboard", async () => {
    const user = userEvent.setup();
    const onActivate = vi.fn();
    wrap(<ResourceDayGrid fromMinute={420} toMinute={1080} groups={groups(onActivate)} labels={labels} ariaLabel="Planer" />);
    await user.click(screen.getByRole("button", { name: "Sperre" }));
    expect(onActivate).toHaveBeenCalledTimes(1);
    // From the page above the grid, Tab reaches the first item; Enter and Space activate it.
    (document.activeElement as HTMLElement | null)?.blur();
    await user.tab();
    expect(screen.getByRole("button", { name: "Auftrag 1001, 09:00 bis 10:00" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(onActivate).toHaveBeenCalledTimes(3);
  });

  it("shows an item nobody may open without offering it: no button, no tab stop", () => {
    const readOnly = groups().map((g) => ({ ...g, rows: g.rows.map((r) => ({ ...r, items: r.items.map((i) => ({ ...i, onActivate: undefined })) })) }));
    wrap(<ResourceDayGrid fromMinute={420} toMinute={1080} groups={readOnly} labels={labels} ariaLabel="Planer" />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    const item = screen.getByRole("img", { name: "Sperre" });
    expect(item).toHaveAttribute("tabindex", "-1");
  });

  it("draws the now line only when now lies on the axis", () => {
    const { rerender } = wrap(
      <ResourceDayGrid fromMinute={420} toMinute={1080} nowMinute={600} groups={groups()} labels={labels} ariaLabel="Planer" />,
    );
    expect(screen.getAllByRole("img", { name: "Jetzt" }).length).toBeGreaterThan(0);
    rerender(
      <MantineProvider theme={testTheme}>
        <ResourceDayGrid fromMinute={420} toMinute={1080} nowMinute={1200} groups={groups()} labels={labels} ariaLabel="Planer" />
      </MantineProvider>,
    );
    expect(screen.queryByRole("img", { name: "Jetzt" })).not.toBeInTheDocument();
  });
});
