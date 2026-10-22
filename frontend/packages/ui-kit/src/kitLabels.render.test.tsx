// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { ViewsAndFilters } from "./datagrid/ViewsAndFilters";
import { FilterBuilder } from "./datagrid/FilterBuilder";
import type { FilterPredicate } from "./datagrid/filterPredicate";
import { RepeatableRowGroup } from "./components/RepeatableRowGroup";
import { OverlayProvider, useOverlay } from "./shell/Overlay";
import {
  FORMER_ENGLISH_DEFAULTS,
  FR_FILTER_BUILDER_LABELS,
  FR_REPEATABLE_ROW_LABELS,
  FR_VIEWS_AND_FILTERS_LABELS,
} from "./test/frenchLabels";

// KAN-364 — FilterBuilder, ViewsAndFilters, RepeatableRowGroup and the
// Overlay merged English defaults under optional labels, and hardcoded some
// accessible names no caller could override ("Edit filter", "Delete view",
// "Close"). Their labels are required now; given French ones, nothing they
// render or announce may be one of the former English defaults. Not checked
// here, because it is not a label yet: FilterBuilder's condition names and a
// saved filter's text, which `filterPredicate.ts` still writes in English
// (KAN-415).

const FIELD = { id: "canton", label: "Canton", type: "text" as const };
const PREDICATE: FilterPredicate = { id: "p1", fieldId: "canton", type: "text", condition: "contains", value: "ZH" };

/** Every visible text node and every accessible name in the document. */
function renderedStrings(): string[] {
  const texts: string[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim();
    if (text) texts.push(text);
  }
  document.body.querySelectorAll("[aria-label], [placeholder]").forEach((el) => {
    texts.push(el.getAttribute("aria-label") ?? "", el.getAttribute("placeholder") ?? "");
  });
  return texts.filter(Boolean);
}

const englishLeft = () => renderedStrings().filter((text) => FORMER_ENGLISH_DEFAULTS.test(text));

describe("kit components show none of their former English defaults (KAN-364)", () => {
  it("ViewsAndFilters and its FilterBuilder: headings, actions and row actions are the caller's", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <ViewsAndFilters
          currentViewName="Tous les véhicules"
          views={[{ id: "v1", name: "Mes véhicules", snapshot: {} }]}
          onApplyView={() => {}}
          onSaveCurrentAsView={() => {}}
          onDeleteView={() => {}}
          onSetDefaultView={() => {}}
          fields={[FIELD]}
          predicates={[PREDICATE]}
          onPredicatesChange={() => {}}
          onResetFilters={() => {}}
          labels={FR_VIEWS_AND_FILTERS_LABELS}
        />
      </MantineProvider>,
    );
    await user.click(screen.getByRole("button", { name: /Tous les véhicules/ }));

    expect(await screen.findByText("Vues")).toBeInTheDocument();
    expect(screen.getByText("Gérer les filtres")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Réinitialiser" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Supprimer la vue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Définir par défaut" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Modifier le filtre" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dupliquer le filtre" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Supprimer le filtre" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Enregistrer la vue actuelle sous…" }));
    expect(screen.getByPlaceholderText("Nom de la vue")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Nouveau filtre…" }));
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Annuler" })).toBeInTheDocument();

    expect(englishLeft()).toEqual([]);
  });

  it("FilterBuilder: the live count is the caller's matchCount", async () => {
    const user = userEvent.setup();
    render(
      <FilterBuilder
        fields={[FIELD]}
        predicates={[]}
        onChange={() => {}}
        onPreviewCount={async () => 1234}
        labels={FR_FILTER_BUILDER_LABELS}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Nouveau filtre…" }));
    expect(await screen.findByText("1234 résultats")).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("RepeatableRowGroup: the row, its menu and the add row are the caller's", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <RepeatableRowGroup
          mode="detail"
          label="Téléphones"
          addLabel="Ajouter un téléphone"
          formerLabel="Anciens"
          typeOptions={[{ value: "mobile", label: "Mobile" }]}
          defaultType="mobile"
          rows={[{ id: "r1", type: "mobile", value: "+41791234567", isPrimary: true, consentGranted: false }]}
          renderValueEditor={(value, onChange) => <input aria-label="Numéro" value={value} onChange={(e) => onChange(e.target.value)} />}
          onCreate={async () => {}}
          onUpdate={async () => {}}
          onDelete={async () => {}}
          labels={FR_REPEATABLE_ROW_LABELS}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("Consentement")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions de la ligne Téléphones" }));
    expect(await screen.findByText("Plus valable")).toBeInTheDocument();
    expect(screen.getByText("Ne fonctionne pas")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: /Ajouter un téléphone/ }));
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Annuler" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("Overlay: the close button is named by the caller", async () => {
    const user = userEvent.setup();
    function Opener() {
      const overlay = useOverlay();
      return (
        <button type="button" onClick={() => overlay.push({ key: "a", content: <p>Fiche client</p> })}>
          ouvrir
        </button>
      );
    }
    render(
      <OverlayProvider closeLabel="Fermer">
        <Opener />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "ouvrir" }));
    expect(screen.getByText("Fiche client")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Fermer" }));
    expect(screen.queryByText("Fiche client")).toBeNull();
    expect(englishLeft()).toEqual([]);
  });
});
