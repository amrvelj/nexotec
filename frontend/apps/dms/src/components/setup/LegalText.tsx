import { Fragment, type ReactNode } from 'react'
import { Blockquote, Stack, Text, Title } from '@mantine/core'

/**
 * KAN-214 — a legal document's body (FR-A-39). The bodies are content
 * written by Nexotec and counsel as markdown; this renders the small subset
 * they use — `#`/`##`/`###` headings, `>` notes, paragraphs and `**bold**` —
 * as React text, never as HTML, so nothing in a body can inject markup.
 */
export function LegalText({ markdown }: { markdown: string }) {
  const blocks = markdown
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter((block) => block !== '')
  return (
    <Stack gap="xs">
      {blocks.map((block, index) => {
        const heading = /^(#{1,3})\s+(.*)$/s.exec(block)
        if (heading) {
          const order = (heading[1].length + 2) as 3 | 4 | 5
          return (
            <Title key={index} order={order}>
              {inline(heading[2])}
            </Title>
          )
        }
        if (block.startsWith('>')) {
          return (
            <Blockquote key={index} color="yellow" p="xs">
              {inline(block.replace(/^>\s?/gm, ''))}
            </Blockquote>
          )
        }
        return (
          <Text key={index} fz="sm" style={{ whiteSpace: 'pre-line' }}>
            {inline(block)}
          </Text>
        )
      })}
    </Stack>
  )
}

function inline(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*)/).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      <Fragment key={index}>{part}</Fragment>
    ),
  )
}
