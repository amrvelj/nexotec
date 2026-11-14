import { Checkbox, Group, Paper, ScrollArea, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { Badge, DocumentText } from '@nexotec/ui-kit'
import type { LegalDocumentRead } from '../../api/types'

/**
 * KAN-214 (FR-A-39, D-A-34) — the documents the manager accepts, each
 * readable in full with its own acceptance tick. Used by «Betrieb aktivieren»
 * and by the re-acceptance screen; the acceptance itself is the caller's
 * POST per document, recorded with the user, the time and the browser.
 */
export function LegalDocumentsToAccept({
  documents,
  accepted,
  onToggle,
}: {
  documents: LegalDocumentRead[]
  accepted: Record<string, boolean>
  onToggle: (documentId: string, value: boolean) => void
}) {
  const { t } = useTranslation()
  return (
    <Stack gap="md">
      {documents.map((document) => (
        <Paper key={document.id} withBorder radius="md" p="md">
          <Stack gap="xs">
            <Group justify="space-between" wrap="nowrap">
              <Text fw={600}>{document.title}</Text>
              <Group gap="xs" wrap="nowrap">
                <Text fz="xs" c="dimmed">
                  {t('setup.legal.version', { version: document.version })}
                </Text>
                {document.draft && (
                  <Badge tone="warning" dot>
                    {t('setup.legal.draft')}
                  </Badge>
                )}
              </Group>
            </Group>
            <ScrollArea.Autosize mah={220} type="auto" aria-label={document.title}>
              <DocumentText markdown={document.bodyMarkdown} />
            </ScrollArea.Autosize>
            <Checkbox
              checked={accepted[document.id] ?? false}
              onChange={(event) => onToggle(document.id, event.currentTarget.checked)}
              label={t('setup.activate.accept', { title: document.title, version: document.version })}
            />
          </Stack>
        </Paper>
      ))}
    </Stack>
  )
}
