import { ActionIcon, Tooltip } from "@mantine/core";
import { ArrowLeft, ArrowRight, ImagePlus, Trash2 } from "lucide-react";
import { purple, radius, slate, spacing, typography } from "../tokens";

export interface MediaGalleryItem {
  id: string;
  url: string;
  position: number;
}

export interface MediaGalleryLabels {
  mainImage: string;
  moveLeft: string;
  moveRight: string;
  remove: string;
  addPhoto: string;
  countOf: (count: number, max: number) => string;
}

interface MediaGalleryBaseProps {
  items: MediaGalleryItem[];
  maxItems: number;
  labels: MediaGalleryLabels;
}

/** KAN-430 — `readOnly` shows the pictures in their order, main image
 * marked, without the add tile or the move and remove controls: for a
 * user who may see the car's photos but not publish (the stock detail's
 * Publikation tab). The handlers belong to the editable gallery only. */
export type MediaGalleryProps = MediaGalleryBaseProps &
  (
    | { readOnly: true }
    | {
        readOnly?: false;
        onReorder: (orderedIds: string[]) => void;
        onRemove: (id: string) => void;
        onAdd: () => void;
      }
  );

/**
 * § Publishing tab — "picture order IS the product; the main image is
 * simply position 1." Deliberately no drag-and-drop (no DnD dependency
 * exists anywhere in this workspace yet) — left/right move buttons
 * reassign position exactly the same way a drag would, just via a
 * click instead of a gesture. The other ui-kit gap this package's own
 * research surfaced, alongside CounterTextarea.
 */
export function MediaGallery(props: MediaGalleryProps) {
  const { items, maxItems, labels } = props;
  const editable = props.readOnly ? null : props;
  const sorted = [...items].sort((a, b) => a.position - b.position);
  const ids = sorted.map((i) => i.id);

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (!editable || target < 0 || target >= ids.length) return;
    const next = [...ids];
    [next[index], next[target]] = [next[target], next[index]];
    editable.onReorder(next);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
      <span style={{ fontSize: typography.meta.size, color: slate[5] }}>
        {labels.countOf(sorted.length, maxItems)}
      </span>
      <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.md }}>
        {sorted.map((item, index) => (
          <div
            key={item.id}
            style={{
              position: "relative",
              width: 140,
              borderRadius: radius.md,
              overflow: "hidden",
              border: `1px solid ${slate[2]}`,
            }}
          >
            <img src={item.url} alt="" style={{ width: "100%", height: 105, objectFit: "cover", display: "block" }} />
            {index === 0 && (
              <span
                style={{
                  position: "absolute",
                  top: 6,
                  left: 6,
                  padding: "2px 8px",
                  borderRadius: radius.full,
                  fontSize: typography.badge.size,
                  fontWeight: typography.badge.weight,
                  backgroundColor: purple[6],
                  color: "white",
                }}
              >
                {labels.mainImage}
              </span>
            )}
            {editable && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: spacing.xs,
                  backgroundColor: slate[0],
                }}
              >
                <Tooltip label={labels.moveLeft}>
                  <ActionIcon variant="subtle" size="sm" disabled={index === 0} onClick={() => move(index, -1)} aria-label={labels.moveLeft}>
                    <ArrowLeft size={14} />
                  </ActionIcon>
                </Tooltip>
                <Tooltip label={labels.remove}>
                  <ActionIcon variant="subtle" size="sm" color="red" onClick={() => editable.onRemove(item.id)} aria-label={labels.remove}>
                    <Trash2 size={14} />
                  </ActionIcon>
                </Tooltip>
                <Tooltip label={labels.moveRight}>
                  <ActionIcon
                    variant="subtle"
                    size="sm"
                    disabled={index === sorted.length - 1}
                    onClick={() => move(index, 1)}
                    aria-label={labels.moveRight}
                  >
                    <ArrowRight size={14} />
                  </ActionIcon>
                </Tooltip>
              </div>
            )}
          </div>
        ))}
        {editable && sorted.length < maxItems && (
          <button
            type="button"
            onClick={editable.onAdd}
            aria-label={labels.addPhoto}
            style={{
              width: 140,
              height: 137,
              borderRadius: radius.md,
              border: `1px dashed ${slate[3]}`,
              background: "none",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: spacing.xs,
              color: slate[5],
              cursor: "pointer",
            }}
          >
            <ImagePlus size={20} />
            <span style={{ fontSize: typography.meta.size }}>{labels.addPhoto}</span>
          </button>
        )}
      </div>
    </div>
  );
}
