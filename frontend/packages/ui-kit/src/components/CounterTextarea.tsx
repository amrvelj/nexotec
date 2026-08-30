import { Textarea } from "@mantine/core";
import { semantic, slate, typography } from "../tokens";

export interface CounterTextareaProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  /** e.g. "Auf der Ergebnisliste werden 125 Zeichen angezeigt." — the
   * marketplace's own display limit is often shorter than the stored
   * field's own max length (AS24's zusatztitel stores 500, shows 125). */
  displayLimitCaption?: string;
  description?: string;
  minRows?: number;
}

/**
 * § Publishing tab — every listing-text field carries a live "0 / 500"
 * counter. One ui-kit gap this package's own research surfaced (no
 * char-counting text input existed anywhere in the library) — added here
 * rather than built inline in the stock package.
 */
export function CounterTextarea({
  label,
  value,
  onChange,
  maxLength,
  displayLimitCaption,
  description,
  minRows = 2,
}: CounterTextareaProps) {
  const overLimit = value.length > maxLength;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: typography.label.size, fontWeight: typography.label.weight, color: slate[7] }}>{label}</span>
        <span style={{ fontSize: typography.meta.size, color: overLimit ? semantic.destructive.text : slate[5] }}>
          {value.length} / {maxLength}
        </span>
      </div>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        minRows={minRows}
        description={description}
        error={overLimit || undefined}
      />
      {displayLimitCaption && (
        <span style={{ fontSize: typography.meta.size, color: slate[5] }}>{displayLimitCaption}</span>
      )}
    </div>
  );
}
