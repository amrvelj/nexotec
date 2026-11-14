import { Fragment, type ReactNode } from "react";
import { Anchor, Blockquote, List, Stack, Text, Title } from "@mantine/core";

/**
 * A document's body written as markdown — the legal documents' ToS and DPA
 * bodies (KAN-214, FR-A-39) and any text counsel or Nexotec delivers the
 * same way (KAN-727). Renders the subset such documents use, as React text,
 * never as HTML, so nothing in a body can inject markup:
 *
 * - `#`, `##`, `###` headings;
 * - `>` notes (a block whose first line starts with `>`; later lines may
 *   leave the `>` out);
 * - bulleted (`-`, `*`) and numbered (`1.`) lists — a line indented under an
 *   item continues it, and a numbered list keeps the number it starts at
 *   (clauses «3. Haftung» … «4. Recht» set apart by body text stay 3 and 4);
 * - paragraphs (a single line break stays a line break);
 * - inline `**bold**`, `*emphasis*` / `_emphasis_` (never inside a word:
 *   `a_b_c` stays as written) and `[text](url)` links.
 *   A link opens only `https:`, `http:` or `mailto:`; any other target
 *   (`javascript:`, a relative path) renders its text alone.
 *
 * The kit ships no string of its own: every word comes from `markdown`.
 */
export function DocumentText({ markdown }: { markdown: string }) {
  const blocks = toBlocks(markdown);
  return (
    <Stack gap="xs">
      {blocks.map((block, index) => {
        switch (block.kind) {
          case "heading":
            return (
              <Title key={index} order={(block.level + 2) as 3 | 4 | 5}>
                {inline(block.text)}
              </Title>
            );
          case "note":
            return (
              <Blockquote key={index} color="yellow" p="xs">
                <Text fz="sm" style={{ whiteSpace: "pre-line" }}>
                  {inline(block.text)}
                </Text>
              </Blockquote>
            );
          case "list":
            return (
              <List
                key={index}
                type={block.ordered ? "ordered" : "unordered"}
                start={block.ordered && block.start !== 1 ? block.start : undefined}
                fz="sm"
                spacing={4}
              >
                {block.items.map((item, itemIndex) => (
                  <List.Item key={itemIndex}>
                    <span style={{ whiteSpace: "pre-line" }}>{inline(item)}</span>
                  </List.Item>
                ))}
              </List>
            );
          default:
            return (
              <Text key={index} fz="sm" style={{ whiteSpace: "pre-line" }}>
                {inline(block.text)}
              </Text>
            );
        }
      })}
    </Stack>
  );
}

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "note"; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: string[] }
  | { kind: "paragraph"; text: string };

const BULLET = /^\s{0,3}[-*]\s+(.*)$/;
const NUMBERED = /^\s{0,3}(\d{1,3})[.)]\s+(.*)$/;

function toBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  const chunks = markdown
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((chunk) => chunk.replace(/^\n+|\s+$/g, ""))
    .filter((chunk) => chunk.trim() !== "");
  for (const chunk of chunks) {
    const lines = chunk.split("\n");
    const heading = /^\s{0,3}(#{1,3})\s+(.*)$/s.exec(chunk);
    if (heading && lines.length === 1) {
      blocks.push({ kind: "heading", level: heading[1].length as 1 | 2 | 3, text: heading[2].trim() });
      continue;
    }
    if (/^\s{0,3}>/.test(lines[0])) {
      blocks.push({ kind: "note", text: lines.map((line) => line.replace(/^\s*>\s?/, "")).join("\n") });
      continue;
    }
    // A chunk may mix a heading line, paragraph lines and list items; split
    // it into runs so each renders as what it is.
    let paragraph: string[] = [];
    let list: { ordered: boolean; start: number; items: string[] } | null = null;
    const flushParagraph = () => {
      if (paragraph.length > 0) blocks.push({ kind: "paragraph", text: paragraph.join("\n").trim() });
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push({ kind: "list", ordered: list.ordered, start: list.start, items: list.items });
      list = null;
    };
    for (const line of lines) {
      const bullet = BULLET.exec(line);
      const numbered = bullet ? null : NUMBERED.exec(line);
      const lineHeading = /^\s{0,3}(#{1,3})\s+(.*)$/.exec(line);
      if (bullet || numbered) {
        const ordered = numbered !== null;
        flushParagraph();
        if (list && list.ordered !== ordered) flushList();
        if (!list) list = { ordered, start: numbered ? Number(numbered[1]) : 1, items: [] };
        list.items.push((bullet ? bullet[1] : (numbered as RegExpExecArray)[2]).trim());
      } else if (lineHeading) {
        flushParagraph();
        flushList();
        blocks.push({ kind: "heading", level: lineHeading[1].length as 1 | 2 | 3, text: lineHeading[2].trim() });
      } else if (list && /^\s+\S/.test(line)) {
        // An indented line continues the item above it.
        const items: string[] = (list as { items: string[] }).items;
        items[items.length - 1] += `\n${line.trim()}`;
      } else {
        flushList();
        paragraph.push(line);
      }
    }
    flushParagraph();
    flushList();
  }
  return blocks;
}

const SAFE_LINK = /^(https?:|mailto:)/i;
// Bold first, then a link, then emphasis — each match is rendered as React
// children, never as markup.
const INLINE = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|(?<![\p{L}\p{N}])_[^_\s][^_]*_(?![\p{L}\p{N}]))/u;

function inline(text: string): ReactNode {
  return text.split(INLINE).map((part, index) => {
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{inline(part.slice(2, -2))}</strong>;
    }
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      const [, label, href] = link;
      return SAFE_LINK.test(href) ? (
        <Anchor key={index} href={href} target="_blank" rel="noopener noreferrer" fz="inherit">
          {label}
        </Anchor>
      ) : (
        <Fragment key={index}>{label}</Fragment>
      );
    }
    if (part.length > 2 && ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_")))) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}
