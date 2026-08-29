import type { ReactNode } from "react";
import { Download, FileWarning } from "lucide-react";
import { purple, radius, shadow, slate, spacing, white } from "../tokens";

export interface DocumentPreviewProps {
  /** A blob: URL or an https URL serving `application/pdf`. Rendered
   * exactly as WeasyPrint produced it — this component draws no layout
   * of its own around the document's own content, only the chrome
   * around it (the "paper" surface, the toolbar). */
  src: string | null;
  title: string;
  /** The document's own correspondence language (§ The document renderer:
   * "renders in the CUSTOMER's correspondence language passed explicitly,
   * never UI locale") — shown as a small badge so it's never confused
   * with the operator's own UI language. */
  correspondenceLanguage?: string;
  loading?: boolean;
  error?: string | null;
  onDownload?: () => void;
  downloadLabel?: string;
  /** Rendered beside the document — the seller-only margin panel (§ ADR-
   * 063: "generating an offer is two steps: build, then review the
   * rendered document ... with the seller-only margin panel BESIDE it,
   * never on it"). Never rendered inside the document itself. */
  marginPanel?: ReactNode;
}

/**
 * § Component Contracts — The document renderer. This is the frontend
 * presentation shell only: it accepts a PDF blob/URL and renders it full-
 * bleed and chrome-free, exactly as ADR-063 describes. Wiring it to the
 * WP-6b template layer (`app.platform.services.document_render.
 * render_document`) is whichever future module first has something to
 * render — that function has no HTTP endpoint yet, since WP-7/8/9 don't
 * exist. Building a second renderer instead of wiring this one to that
 * function, whenever that day comes, is the mistake this component's own
 * existence is meant to prevent.
 */
export function DocumentPreview({
  src,
  title,
  correspondenceLanguage,
  loading,
  error,
  onDownload,
  downloadLabel = "Download",
  marginPanel,
}: DocumentPreviewProps) {
  return (
    <div style={{ display: "flex", gap: spacing.lg, alignItems: "flex-start" }}>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          borderRadius: radius.lg,
          border: `1px solid ${slate[2]}`,
          overflow: "hidden",
          backgroundColor: white,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: spacing.sm,
            padding: `${spacing.sm} ${spacing.md}`,
            borderBottom: `1px solid ${slate[2]}`,
            backgroundColor: slate[0],
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: slate[7], flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {title}
          </span>
          {correspondenceLanguage && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.5px",
                textTransform: "uppercase",
                color: purple[7],
                backgroundColor: purple[1],
                borderRadius: radius.full,
                padding: "2px 8px",
              }}
            >
              {correspondenceLanguage}
            </span>
          )}
          {onDownload && (
            <button
              type="button"
              onClick={onDownload}
              disabled={!src}
              aria-label={downloadLabel}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                border: "none",
                background: "none",
                color: purple[6],
                fontSize: 12,
                fontWeight: 600,
                cursor: src ? "pointer" : "default",
                opacity: src ? 1 : 0.4,
              }}
            >
              <Download size={14} />
              {downloadLabel}
            </button>
          )}
        </div>

        {/* Full-bleed, paper-like, chrome-free — no margin/padding of our
            own around the PDF's own content; the browser's native PDF
            viewer inside the iframe owns everything past the toolbar
            above. */}
        <div style={{ flex: 1, minHeight: 480, boxShadow: shadow.sm, position: "relative" }}>
          {loading && <CenteredMessage>Loading…</CenteredMessage>}
          {!loading && error && (
            <CenteredMessage>
              <FileWarning size={24} color={slate[4]} />
              <span style={{ marginTop: spacing.xs }}>{error}</span>
            </CenteredMessage>
          )}
          {!loading && !error && src && (
            <iframe title={title} src={src} style={{ width: "100%", height: "100%", minHeight: 480, border: "none", display: "block" }} />
          )}
        </div>
      </div>

      {marginPanel && (
        <div style={{ flex: "0 0 280px" }}>{marginPanel}</div>
      )}
    </div>
  );
}

function CenteredMessage({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: slate[5],
        fontSize: 14,
      }}
    >
      {children}
    </div>
  );
}
