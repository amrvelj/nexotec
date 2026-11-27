import { Fragment, type CSSProperties } from "react";
import { Box, Text, UnstyledButton } from "@mantine/core";
import { RotateCcw } from "lucide-react";
import { purple, radius, semantic, slate, slate25, white } from "../tokens";

/**
 * KAN-809 (ADR: the kit's resource day grid) — one day as rows of resources
 * against a time axis: the planner's PL1. The kit draws, the caller decides:
 * every string arrives through props (the kit ships none, ui-i18n.md), every
 * time is a minute of the local day (0 = midnight; the caller converts its
 * instants), and what a click on an item does is the caller's.
 *
 * - Groups of rows (people, workplaces), each row a label and a track.
 * - Items in a row that overlap are laid in lanes, so none hides another.
 * - `unavailable` spans (outside a person's working hours) are hatched — and
 *   carry their meaning in text too (`labels.unavailable(span)` as their title
 *   and accessible name, with the span's times — KAN-846), never by pattern
 *   alone.
 * - An item marked `flagLabel` («neu einplanen») gets a dashed destructive
 *   border AND the label as text.
 * - Every item the caller lets open (`onActivate`) is a button in the tab
 *   order, rows in order and items by start; Enter or Space activates it. One
 *   without is shown and named, never offered. The track scrolls horizontally on a
 *   narrow screen; the labels stay put.
 */

export type DayGridTone = "booking" | "slot" | "block";

export interface DayGridItem {
  id: string;
  startMinute: number;
  endMinute: number;
  title: string;
  detail?: string;
  tone: DayGridTone;
  /** Shown as text on the item and as its dashed border («neu einplanen»). */
  flagLabel?: string;
  /** The whole item read aloud: what, when, which resources. */
  ariaLabel: string;
  onActivate?: () => void;
}

export interface DayGridSpan {
  startMinute: number;
  endMinute: number;
}

export interface DayGridRow {
  id: string;
  title: string;
  subtitle?: string;
  unavailable?: DayGridSpan[];
  items: DayGridItem[];
}

export interface DayGridGroup {
  id: string;
  label: string;
  rows: DayGridRow[];
}

export interface ResourceDayGridLabels {
  /** The header of the label column («Ressource»). */
  resource: string;
  /** The text for a hatched span, with its times («Nicht im Dienst, 12:00–13:00», KAN-846). */
  unavailable: (span: DayGridSpan) => string;
  /** The now line's accessible name («Jetzt»). */
  now: string;
  /** An hour's label on the axis («08:00»). */
  hour: (minute: number) => string;
}

export interface ResourceDayGridProps {
  /** The first and the last minute the axis shows; whole hours. */
  fromMinute: number;
  toMinute: number;
  /** The planner's grid (O6) in minutes: the fine lines. None: hours only. */
  gridMinutes?: number | null;
  /** The minute of now, when the day shown is today. */
  nowMinute?: number | null;
  groups: DayGridGroup[];
  labels: ResourceDayGridLabels;
  /** The accessible name of the whole grid («Planer, Montag, 1. März 2027»). */
  ariaLabel: string;
}

const LABEL_WIDTH = 220;
const HOUR_WIDTH = 88;
const LANE_HEIGHT = 46;
const ROW_PADDING = 6;

const TONES: Record<DayGridTone, { background: string; border: string; color: string }> = {
  booking: { background: purple[0], border: purple[3], color: purple[9] },
  slot: { background: semantic.informational.surface, border: semantic.informational.border, color: semantic.informational.ink },
  block: { background: slate[1], border: slate[3], color: slate[7] },
};

/** Lanes for the items of one row: an item goes into the first lane whose last
 * item has ended (half-open: touching items share a lane). */
export function layLanes<T extends { startMinute: number; endMinute: number }>(items: readonly T[]): { item: T; lane: number }[] {
  const sorted = [...items].sort((a, b) => a.startMinute - b.startMinute || a.endMinute - b.endMinute);
  const laneEnds: number[] = [];
  return sorted.map((item) => {
    let lane = laneEnds.findIndex((end) => end <= item.startMinute);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(item.endMinute);
    } else {
      laneEnds[lane] = item.endMinute;
    }
    return { item, lane };
  });
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high);
}

export function ResourceDayGrid({
  fromMinute,
  toMinute,
  gridMinutes,
  nowMinute,
  groups,
  labels,
  ariaLabel,
}: ResourceDayGridProps) {
  const span = Math.max(toMinute - fromMinute, 60);
  const trackWidth = (span / 60) * HOUR_WIDTH;
  const x = (minute: number) => ((clamp(minute, fromMinute, toMinute) - fromMinute) / span) * trackWidth;
  const hours: number[] = [];
  for (let minute = fromMinute; minute < toMinute; minute += 60) hours.push(minute);
  const fine = gridMinutes && gridMinutes > 0 && gridMinutes < 60 ? gridMinutes : null;
  const lines: CSSProperties = {
    backgroundImage: [
      `repeating-linear-gradient(to right, ${slate[2]} 0 1px, transparent 1px ${HOUR_WIDTH}px)`,
      fine ? `repeating-linear-gradient(to right, ${slate[1]} 0 1px, transparent 1px ${(HOUR_WIDTH * fine) / 60}px)` : "",
    ]
      .filter(Boolean)
      .join(", "),
  };
  const showNow = nowMinute !== null && nowMinute !== undefined && nowMinute >= fromMinute && nowMinute <= toMinute;

  return (
    <Box
      role="group"
      aria-label={ariaLabel}
      style={{ border: `1px solid ${slate[2]}`, borderRadius: radius.lg, background: white, overflowX: "auto" }}
    >
      <Box style={{ display: "grid", gridTemplateColumns: `${LABEL_WIDTH}px ${trackWidth}px`, minWidth: LABEL_WIDTH + trackWidth }}>
        {/* The axis */}
        <Box
          style={{
            position: "sticky",
            left: 0,
            zIndex: 2,
            background: slate25,
            borderBottom: `1px solid ${slate[2]}`,
            borderRight: `1px solid ${slate[2]}`,
            padding: "8px 12px",
          }}
        >
          <Text size="xs" fw={600} c={slate[5]} tt="uppercase">
            {labels.resource}
          </Text>
        </Box>
        <Box style={{ position: "relative", background: slate25, borderBottom: `1px solid ${slate[2]}`, height: 34 }}>
          {hours.map((minute) => (
            <Text
              key={minute}
              size="xs"
              fw={600}
              c={slate[5]}
              style={{ position: "absolute", left: x(minute) + 6, top: 9 }}
            >
              {labels.hour(minute)}
            </Text>
          ))}
        </Box>

        {groups.map((group) => (
          <Fragment key={group.id}>
            <Box
              role="presentation"
              style={{
                gridColumn: "1 / span 2",
                background: slate[1],
                borderBottom: `1px solid ${slate[2]}`,
                padding: "4px 12px",
                position: "sticky",
                left: 0,
              }}
            >
              <Text size="xs" fw={700} c={slate[6]} tt="uppercase">
                {group.label}
              </Text>
            </Box>
            {group.rows.map((row) => {
              const laid = layLanes(row.items);
              const lanes = Math.max(1, ...laid.map(({ lane }) => lane + 1));
              const height = lanes * LANE_HEIGHT + ROW_PADDING * 2;
              return (
                <Fragment key={row.id}>
                  <Box
                    style={{
                      position: "sticky",
                      left: 0,
                      zIndex: 1,
                      background: white,
                      borderBottom: `1px solid ${slate[2]}`,
                      borderRight: `1px solid ${slate[2]}`,
                      padding: "8px 12px",
                      minHeight: height,
                    }}
                  >
                    <Text size="sm" fw={600} c={slate[9]}>
                      {row.title}
                    </Text>
                    {row.subtitle && (
                      <Text size="xs" c={slate[5]}>
                        {row.subtitle}
                      </Text>
                    )}
                  </Box>
                  <Box
                    data-row={row.id}
                    style={{ position: "relative", height, borderBottom: `1px solid ${slate[2]}`, ...lines }}
                  >
                    {(row.unavailable ?? []).map((gap) =>
                      gap.endMinute > fromMinute && gap.startMinute < toMinute ? (
                        <Box
                          key={`${gap.startMinute}-${gap.endMinute}`}
                          title={labels.unavailable(gap)}
                          role="img"
                          aria-label={labels.unavailable(gap)}
                          data-unavailable
                          style={{
                            position: "absolute",
                            top: 0,
                            bottom: 0,
                            left: x(gap.startMinute),
                            width: x(gap.endMinute) - x(gap.startMinute),
                            backgroundImage: `repeating-linear-gradient(135deg, ${slate[1]} 0 6px, transparent 6px 12px)`,
                          }}
                        />
                      ) : null,
                    )}
                    {laid.map(({ item, lane }) => {
                      const tone = TONES[item.tone];
                      const left = x(item.startMinute);
                      const width = Math.max(x(item.endMinute) - left - 2, 14);
                      return (
                        <UnstyledButton
                          key={item.id}
                          // An item nobody may open is shown, not offered: no button, no tab stop.
                          component={item.onActivate ? "button" : "div"}
                          {...(item.onActivate ? { onClick: item.onActivate } : { role: "img", tabIndex: -1 })}
                          aria-label={item.ariaLabel}
                          data-item={item.id}
                          style={{
                            position: "absolute",
                            top: ROW_PADDING + lane * LANE_HEIGHT,
                            left: left + 1,
                            width,
                            height: LANE_HEIGHT - 4,
                            padding: "3px 6px",
                            overflow: "hidden",
                            borderRadius: radius.sm,
                            background: tone.background,
                            border: item.flagLabel ? `2px dashed ${semantic.destructive.text}` : `1px solid ${tone.border}`,
                            color: tone.color,
                            backgroundImage:
                              item.tone === "block"
                                ? `repeating-linear-gradient(135deg, ${slate[2]} 0 4px, transparent 4px 10px)`
                                : undefined,
                          }}
                        >
                          <Text size="xs" fw={600} lineClamp={1} c="inherit">
                            {item.title}
                          </Text>
                          <Text size="xs" lineClamp={1} c="inherit">
                            {item.flagLabel ? (
                              <span style={{ color: semantic.destructive.ink, fontWeight: 600 }}>
                                <RotateCcw size={10} style={{ marginRight: 3, verticalAlign: -1 }} aria-hidden />
                                <span>{item.flagLabel}</span>
                              </span>
                            ) : null}
                            {item.flagLabel && item.detail ? (
                              <span aria-hidden>{" · "}</span>
                            ) : null}
                            {item.detail}
                          </Text>
                        </UnstyledButton>
                      );
                    })}
                    {showNow && (
                      <Box
                        role="img"
                        aria-label={labels.now}
                        style={{
                          position: "absolute",
                          top: 0,
                          bottom: 0,
                          left: x(nowMinute as number),
                          width: 2,
                          background: semantic.destructive.text,
                          pointerEvents: "none",
                        }}
                      />
                    )}
                  </Box>
                </Fragment>
              );
            })}
          </Fragment>
        ))}
      </Box>
    </Box>
  );
}
