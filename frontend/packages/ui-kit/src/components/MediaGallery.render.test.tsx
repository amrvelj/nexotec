// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import type { ReactElement } from "react";
import { MediaGallery, type MediaGalleryLabels } from "./MediaGallery";
import { testTheme } from "../test/testTheme";

// KAN-430 — a user who may see a car's photos but not publish gets the
// pictures in order with the main image marked, and no control that would
// write: no add tile, no move or remove buttons.

const LABELS: MediaGalleryLabels = {
  mainImage: "Image principale",
  moveLeft: "À gauche",
  moveRight: "À droite",
  remove: "Supprimer",
  addPhoto: "Ajouter une photo",
  countOf: (count, max) => `${count} / ${max}`,
};
const ITEMS = [
  { id: "b", url: "https://cdn.example.ch/2.jpg", position: 2 },
  { id: "a", url: "https://cdn.example.ch/1.jpg", position: 1 },
];

function renderGallery(ui: ReactElement) {
  return render(
    <MantineProvider theme={testTheme} env="test">
      {ui}
    </MantineProvider>,
  );
}

describe("MediaGallery", () => {
  it("read-only shows the pictures in order and offers nothing that writes", () => {
    const { container } = renderGallery(<MediaGallery items={ITEMS} maxItems={16} labels={LABELS} readOnly />);

    expect([...container.querySelectorAll("img")].map((img) => img.getAttribute("src"))).toEqual([
      "https://cdn.example.ch/1.jpg",
      "https://cdn.example.ch/2.jpg",
    ]);
    expect(screen.getByText("Image principale")).toBeInTheDocument();
    expect(screen.getByText("2 / 16")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("editable offers the add tile and the move and remove controls", () => {
    renderGallery(
      <MediaGallery items={ITEMS} maxItems={16} labels={LABELS} onAdd={vi.fn()} onRemove={vi.fn()} onReorder={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "Ajouter une photo" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Supprimer" })).toHaveLength(2);
  });
});
