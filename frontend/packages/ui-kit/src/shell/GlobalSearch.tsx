import { useEffect, useRef, useState } from "react";
import { Modal } from "@mantine/core";
import { Clock, Plus, Search } from "lucide-react";
import { purple, radius, shimmer, slate, spacing, white } from "../tokens";

export interface GlobalSearchResultItem {
  id: string;
  /** Rendered first, in mono — a customer number, a VIN. Same "identifiers
   * rank first" rule as § Component Contracts — The picker. */
  identifier?: string;
  label: string;
  sublabel?: string;
  href: string;
}

export interface GlobalSearchGroup {
  key: string;
  /** Translated group heading, e.g. "Customers", "Vehicles". */
  label: string;
  items: GlobalSearchResultItem[];
}

export interface GlobalSearchCreateAction {
  label: string;
  onClick: () => void;
}

export interface GlobalSearchProps {
  /**
   * This component owns the trigger, the dialog, the 250ms debounce and
   * all six states (§ FR-UI-08) — it never talks to an API itself. The
   * caller decides which entities are searched and how results are
   * grouped, same "one component, callers supply the data" split as
   * `Picker`.
   */
  onSearch: (query: string) => Promise<GlobalSearchGroup[]>;
  onSelect: (item: GlobalSearchResultItem) => void;
  /** Shown in the idle state, before the user has typed anything. */
  recents?: GlobalSearchResultItem[];
  /** The no-match-with-create state's affordance is entity-specific (only
   * the caller knows which entities admit a "create new" from here), so
   * it supplies the actions rather than this component guessing one. */
  createActions?: GlobalSearchCreateAction[];
  placeholder?: string;
  recentsLabel?: string;
  noResultsLabel?: string;
  errorLabel?: string;
}

type SearchState =
  | { kind: "idle" }
  | { kind: "typing" }
  | { kind: "loading" }
  | { kind: "results"; groups: GlobalSearchGroup[] }
  | { kind: "empty" }
  | { kind: "error" };

const DEBOUNCE_MS = 250;

/**
 * § Application Shell — Topbar: "the breadcrumb carries the left edge,
 * global search the centre, nothing else." ⌘K/Ctrl+K opens it from
 * anywhere; the topbar's own rendering of this component is just the
 * closed-state trigger, capped at 560px per § Layout dimensions.
 */
export function GlobalSearch({
  onSearch,
  onSelect,
  recents = [],
  createActions = [],
  placeholder = "Search…",
  recentsLabel = "Recent",
  noResultsLabel = "No matches",
  errorLabel = "Search failed — try again.",
}: GlobalSearchProps) {
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ kind: "idle" });
  const [activeIndex, setActiveIndex] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestIdRef = useRef(0);

  // Global ⌘K / Ctrl+K — self-owned, same convention as AppShell's own `[`
  // sidebar-toggle listener.
  useEffect(() => {
    const handleKeydown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpened(true);
      }
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  useEffect(() => {
    if (!opened) return;
    setQuery("");
    setState({ kind: "idle" });
    setActiveIndex(0);
  }, [opened]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (query === "") {
      setState({ kind: "idle" });
      return;
    }

    setState({ kind: "typing" });
    debounceRef.current = setTimeout(() => {
      const requestId = ++requestIdRef.current;
      setState({ kind: "loading" });
      onSearch(query)
        .then((groups) => {
          if (requestId !== requestIdRef.current) return; // a newer keystroke won
          const hasResults = groups.some((g) => g.items.length > 0);
          setState(hasResults ? { kind: "results", groups } : { kind: "empty" });
          setActiveIndex(0);
        })
        .catch(() => {
          if (requestId !== requestIdRef.current) return;
          setState({ kind: "error" });
        });
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const flatItems = state.kind === "results" ? state.groups.flatMap((g) => g.items) : [];

  const select = (item: GlobalSearchResultItem) => {
    setOpened(false);
    onSelect(item);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, flatItems.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = flatItems[activeIndex];
      if (item) select(item);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpened(true)}
        aria-label={placeholder}
        style={{
          width: "100%",
          maxWidth: 560,
          margin: "0 auto",
          display: "flex",
          alignItems: "center",
          gap: spacing.sm,
          padding: `${spacing.sm} ${spacing.md}`,
          borderRadius: radius.md,
          border: `1px solid ${slate[2]}`,
          background: slate[0],
          color: slate[5],
          fontSize: 14,
          cursor: "pointer",
        }}
      >
        <Search size={16} strokeWidth={2} aria-hidden="true" />
        <span style={{ flex: 1, textAlign: "left" }}>{placeholder}</span>
        <kbd
          style={{
            fontSize: 11,
            fontFamily: "inherit",
            color: slate[4],
            border: `1px solid ${slate[2]}`,
            borderRadius: radius.sm,
            padding: "1px 6px",
            background: white,
          }}
        >
          ⌘K
        </kbd>
      </button>

      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        withCloseButton={false}
        size={560}
        padding={0}
        radius="lg"
      >
        <div style={{ position: "relative", borderBottom: `1px solid ${slate[2]}` }}>
          <Search
            size={16}
            color={slate[4]}
            style={{ position: "absolute", left: spacing.md, top: "50%", transform: "translateY(-50%)" }}
            aria-hidden="true"
          />
          <input
            autoFocus
            type="text"
            role="combobox"
            aria-expanded={state.kind === "results"}
            value={query}
            placeholder={placeholder}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            style={{
              width: "100%",
              boxSizing: "border-box",
              border: "none",
              outline: "none",
              padding: `${spacing.lg} ${spacing.lg} ${spacing.lg} 44px`,
              fontSize: 16,
            }}
          />
        </div>

        <div style={{ maxHeight: 420, overflowY: "auto", padding: spacing.sm }}>
          {state.kind === "idle" &&
            (recents.length > 0 ? (
              <>
                <GroupHeading label={recentsLabel} />
                {recents.map((item) => (
                  <ResultRow key={item.id} item={item} icon={<Clock size={14} color={slate[4]} />} onClick={() => select(item)} />
                ))}
              </>
            ) : null)}

          {state.kind === "typing" && <StatusLine>…</StatusLine>}

          {state.kind === "loading" && <SkeletonRows />}

          {state.kind === "results" &&
            state.groups.map((group) => (
              <div key={group.key}>
                <GroupHeading label={group.label} />
                {group.items.map((item) => {
                  const flatIndex = flatItems.indexOf(item);
                  return (
                    <ResultRow
                      key={item.id}
                      item={item}
                      active={flatIndex === activeIndex}
                      onMouseEnter={() => setActiveIndex(flatIndex)}
                      onClick={() => select(item)}
                    />
                  );
                })}
              </div>
            ))}

          {state.kind === "empty" && (
            <>
              <StatusLine>{noResultsLabel}</StatusLine>
              {createActions.map((action) => (
                <button
                  key={action.label}
                  type="button"
                  onClick={action.onClick}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: spacing.xs,
                    width: "100%",
                    border: "none",
                    background: "none",
                    cursor: "pointer",
                    padding: `${spacing.sm} ${spacing.md}`,
                    color: purple[6],
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  <Plus size={14} />
                  {action.label}
                </button>
              ))}
            </>
          )}

          {state.kind === "error" && <StatusLine>{errorLabel}</StatusLine>}
        </div>
      </Modal>
    </>
  );
}

function GroupHeading({ label }: { label: string }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.7px",
        textTransform: "uppercase",
        color: slate[4],
        padding: `${spacing.sm} ${spacing.md} ${spacing.xs}`,
      }}
    >
      {label}
    </div>
  );
}

function StatusLine({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: spacing.md, color: slate[5], fontSize: 13 }}>{children}</div>;
}

function SkeletonRows() {
  return (
    <div aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: spacing.xs, padding: spacing.sm }}>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ height: 36, borderRadius: radius.sm, background: shimmer }} />
      ))}
    </div>
  );
}

function ResultRow({
  item,
  icon,
  active,
  onMouseEnter,
  onClick,
}: {
  item: GlobalSearchResultItem;
  icon?: React.ReactNode;
  active?: boolean;
  onMouseEnter?: () => void;
  onClick: () => void;
}) {
  return (
    <div
      role="option"
      aria-selected={active}
      onMouseEnter={onMouseEnter}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: spacing.sm,
        padding: `${spacing.sm} ${spacing.md}`,
        borderRadius: radius.sm,
        cursor: "pointer",
        backgroundColor: active ? purple[0] : undefined,
      }}
    >
      {icon}
      {item.identifier && (
        <span style={{ fontFamily: "monospace", fontSize: 12, color: slate[6], flexShrink: 0 }}>{item.identifier}</span>
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: slate[9], overflow: "hidden", textOverflow: "ellipsis" }}>
          {item.label}
        </div>
        {item.sublabel && <div style={{ fontSize: 12, color: slate[5] }}>{item.sublabel}</div>}
      </div>
    </div>
  );
}
