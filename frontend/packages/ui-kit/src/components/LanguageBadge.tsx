import { slate } from "../tokens";

export type CustomerLanguage = "de" | "fr" | "it" | "en";

/**
 * "Language: Slate, 10px, +0.5px tracking — DE FR IT EN" — deliberately
 * not built on the shared Badge primitive: this is its own smaller, pill-
 * less variant per § Badges and Status, not a tone of the standard badge.
 */
export function LanguageBadge({ language }: { language: CustomerLanguage }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: "10px",
        fontWeight: 600,
        letterSpacing: "0.5px",
        color: slate[6],
        textTransform: "uppercase",
      }}
    >
      {language}
    </span>
  );
}
