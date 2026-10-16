// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectionStatusBadge } from "./ConnectionStatusBadge";
import { LifecycleStatusBadge } from "./LifecycleStatusBadge";
import { ValuationSourceBadge } from "./ValuationSourceBadge";
import { CustomerTypeBadge } from "./CustomerTypeBadge";
import { StockLifecycleBadge } from "./StockLifecycleBadge";
import { StockConditionBadge } from "./StockConditionBadge";
import { StockReservationBadge } from "./StockReservationBadge";
import { SalesStatusBadge } from "./SalesStatusBadge";
import { SalesTypeBadge } from "./SalesTypeBadge";
import { ValuationStatusBadge } from "./ValuationStatusBadge";

// KAN-110 / KAN-181 — a kit badge never carries a user-visible default
// string: `label` is required and is the only text the badge renders. The
// kit cannot import the app's i18n, so the app passes the translation; these
// render each badge under a French label and prove the former German /
// English defaults are gone from the DOM.

describe("status badges render only the label they are given", () => {
  it("ConnectionStatusBadge: the French label, never the former German default", () => {
    render(<ConnectionStatusBadge status="connected" label="Connectée" />);
    expect(screen.getByText("Connectée")).toBeInTheDocument();
    expect(screen.queryByText("Verbunden")).not.toBeInTheDocument();
  });

  it("LifecycleStatusBadge: the French label, never the former English default", () => {
    render(<LifecycleStatusBadge status="do_not_contact" label="Ne pas contacter" />);
    expect(screen.getByText("Ne pas contacter")).toBeInTheDocument();
    expect(screen.queryByText("Do not contact")).not.toBeInTheDocument();
  });

  it("LifecycleStatusBadge keeps the struck-through tone for merged, label aside", () => {
    render(<LifecycleStatusBadge status="merged" label="Fusionné" />);
    expect(screen.getByText("Fusionné")).toHaveStyle({ textDecoration: "line-through" });
  });

  it("ValuationSourceBadge: the French label, never the former German default", () => {
    render(<ValuationSourceBadge source="manual" label="Manuel" />);
    expect(screen.getByText("Manuel")).toBeInTheDocument();
    expect(screen.queryByText("Manuell")).not.toBeInTheDocument();
  });

  // KAN-334 — the seven badges that still carried a default label.
  it.each([
    ["CustomerTypeBadge", () => <CustomerTypeBadge type="business" label="Entreprise" />, "Entreprise", "Business"],
    ["StockLifecycleBadge", () => <StockLifecycleBadge status="in_stock" label="En stock" />, "En stock", "In stock"],
    ["StockConditionBadge", () => <StockConditionBadge condition="demo" label="Démonstration" />, "Démonstration", "Vorführwagen"],
    ["StockReservationBadge", () => <StockReservationBadge state="reserved" label="Réservé" />, "Réservé", "Reserved"],
    ["SalesStatusBadge", () => <SalesStatusBadge status="confirmed" label="Confirmé" />, "Confirmé", "Bestätigt"],
    ["SalesTypeBadge", () => <SalesTypeBadge entityType="offer" label="Offre" />, "Offre", "Offerte"],
    ["ValuationStatusBadge", () => <ValuationStatusBadge status="valid" label="Valide" />, "Valide", "Gültig"],
  ])("%s: the French label, never the former default", (_name, ui, label, formerDefault) => {
    render(ui());
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(formerDefault)).not.toBeInTheDocument();
  });
});
