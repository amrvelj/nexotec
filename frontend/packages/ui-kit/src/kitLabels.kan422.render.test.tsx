// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { DataGrid } from "./datagrid/DataGrid";
import type { GridColumnDef } from "./datagrid/types";
import { Wizard } from "./wizard/Wizard";
import { GlobalSearch } from "./shell/GlobalSearch";
import { Picker } from "./components/Picker";
import { InlineEditField } from "./detail/InlineEditField";
import { DocumentPreview } from "./components/DocumentPreview";
import { PresentMode } from "./components/PresentMode";
import {
  FORMER_ENGLISH_DEFAULTS,
  FR_DATA_GRID_LABELS,
  FR_GLOBAL_SEARCH_LABELS,
  FR_WIZARD_LABELS,
} from "./test/frenchLabels";

// KAN-422 (KAN-364's remainder) — DataGrid, Wizard, GlobalSearch, Picker,
// InlineEditField, DocumentPreview and PresentMode merged English defaults
// under optional labels (DocumentPreview also hardcoded «Loading…»). Their
// labels are required now; given French ones, nothing they render or
// announce may be one of the former English defaults.

// jsdom has no layout: render every row instead of windowing to none.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize();
    return {
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    };
  },
}));

function renderedStrings(): string[] {
  const texts: string[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim();
    if (text) texts.push(text);
  }
  document.body.querySelectorAll("[aria-label], [placeholder], [title]").forEach((el) => {
    texts.push(el.getAttribute("aria-label") ?? "", el.getAttribute("placeholder") ?? "", el.getAttribute("title") ?? "");
  });
  return texts.filter(Boolean);
}

const englishLeft = () => renderedStrings().filter((text) => FORMER_ENGLISH_DEFAULTS.test(text));

interface Row {
  id: string;
  name: string;
}

const columns: GridColumnDef<Row>[] = [{ id: "name", header: "Nom", cell: ({ row }) => row.original.name }];

describe("kit components show none of their former English defaults (KAN-422)", () => {
  it("DataGrid: the count, the selection and the row menu are named by the caller", () => {
    render(
      <MantineProvider>
        <DataGrid<Row>
          columns={columns}
          rows={[{ id: "a", name: "Müller Hans" }]}
          getRowId={(r) => r.id}
          sort={[]}
          onSortChange={() => {}}
          density="default"
          loading={false}
          fetchingNextPage
          hasNextPage
          onLoadMore={() => {}}
          total={12}
          totalIsEstimate={false}
          isFiltered={false}
          emptyState={{ icon: null, title: "", description: "" }}
          rowActions={() => ({ navigate: [{ label: "Ouvrir", icon: null, onClick: () => {} }] })}
          selection={{
            selectedIds: new Set(),
            onSelectionChange: () => {},
            selectAllLabel: "Tout sélectionner",
            selectRowLabel: (row) => `Sélectionner la ligne : ${row.name}`,
          }}
          labels={FR_DATA_GRID_LABELS}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("1 affichés sur 12")).toBeInTheDocument();
    expect(screen.getByText("Chargement…")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Tout sélectionner" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Sélectionner la ligne : Müller Hans" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Actions de la ligne" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("DataGrid: the retry button is the caller's", () => {
    render(
      <MantineProvider>
        <DataGrid<Row>
          columns={columns}
          rows={[]}
          getRowId={(r) => r.id}
          sort={[]}
          onSortChange={() => {}}
          density="default"
          loading={false}
          fetchingNextPage={false}
          hasNextPage={false}
          onLoadMore={() => {}}
          error="Le chargement a échoué."
          onRetry={() => {}}
          total={0}
          totalIsEstimate={false}
          isFiltered={false}
          emptyState={{ icon: null, title: "", description: "" }}
          labels={FR_DATA_GRID_LABELS}
        />
      </MantineProvider>,
    );
    expect(screen.getByRole("button", { name: "Réessayer" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("Wizard: the four footer buttons are the caller's", () => {
    render(
      <MantineProvider>
        <Wizard
          steps={[
            { id: "a", label: "Identité" },
            { id: "b", label: "Contact" },
          ]}
          activeIndex={1}
          onBack={() => {}}
          onNext={() => {}}
          onCancel={() => {}}
          {...FR_WIZARD_LABELS}
        >
          <p>Étape</p>
        </Wizard>
      </MantineProvider>,
    );
    for (const name of ["Annuler", "Retour", "Créer"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(englishLeft()).toEqual([]);
  });

  it("Wizard: the first step's «next» button is the caller's", () => {
    render(
      <MantineProvider>
        <Wizard
          steps={[
            { id: "a", label: "Identité" },
            { id: "b", label: "Contact" },
          ]}
          activeIndex={0}
          onBack={() => {}}
          onNext={() => {}}
          onCancel={() => {}}
          {...FR_WIZARD_LABELS}
        >
          <p>Étape</p>
        </Wizard>
      </MantineProvider>,
    );
    expect(screen.getByRole("button", { name: "Suivant" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("GlobalSearch: the trigger, the recents heading and the empty line are the caller's", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <GlobalSearch
          onSearch={async () => []}
          onSelect={() => {}}
          recents={[{ id: "r1", label: "Müller Hans", href: "/customers/r1" }]}
          {...FR_GLOBAL_SEARCH_LABELS}
        />
      </MantineProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Rechercher…" }));
    expect(await screen.findByText("Récents")).toBeInTheDocument();
    await user.keyboard("zzz");
    expect(await screen.findByText("Aucun résultat", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("Picker: the empty line and the create path are the caller's", () => {
    render(
      <MantineProvider>
        <Picker
          rows={[]}
          query="zzz"
          onQueryChange={() => {}}
          onSelect={() => {}}
          emptyLabel="Aucune correspondance"
          onCreateNew={() => {}}
          createLabel="Nouveau client"
        />
      </MantineProvider>,
    );
    expect(screen.getByText("Aucune correspondance")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nouveau client/ })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("InlineEditField: an empty value reads as the caller's label", () => {
    render(
      <MantineProvider>
        <InlineEditField
          value=""
          isEmpty
          emptyLabel="Non renseigné"
          onSave={async () => {}}
          describeError={() => "Erreur"}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("Non renseigné")).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("DocumentPreview: the loading line and the download button are the caller's", () => {
    render(
      <DocumentPreview
        src={null}
        title="Offre"
        loading
        loadingLabel="Chargement du document…"
        onDownload={() => {}}
        downloadLabel="Télécharger"
      />,
    );
    expect(screen.getByText("Chargement du document…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Télécharger" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });

  it("PresentMode: the exit control is named by the caller", () => {
    render(
      <PresentMode onExit={() => {}} exitLabel="Quitter la présentation">
        <p>Véhicule</p>
      </PresentMode>,
    );
    expect(screen.getByRole("button", { name: "Quitter la présentation" })).toBeInTheDocument();
    expect(englishLeft()).toEqual([]);
  });
});
