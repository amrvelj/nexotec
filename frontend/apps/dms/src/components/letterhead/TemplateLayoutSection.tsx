import { useId } from 'react'
import { ActionIcon, Alert, Card, Checkbox, Group, Input, SegmentedControl, Stack, Switch, Text } from '@mantine/core'
import { ArrowLeft, ArrowRight, LayoutTemplate } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type {
  AddressWindow,
  DocumentTemplateRead,
  DocumentTemplateUpdate,
  FooterColumn,
  HeaderLayout,
  LogoSize,
} from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'

const HEADER_LAYOUTS: HeaderLayout[] = ['logo_left', 'logo_right', 'logo_centred']
const LOGO_SIZES: LogoSize[] = ['s', 'm', 'l']
const ADDRESS_WINDOWS: AddressWindow[] = ['left', 'right']
const FOOTER_COLUMNS: FooterColumn[] = ['company', 'contact', 'banking', 'register']

/** The footer's order as shown: the printed columns in their order, then the
 * ones not printed, in the shipped order. */
function footerRows(selected: FooterColumn[]): { column: FooterColumn; printed: boolean }[] {
  return [
    ...selected.map((column) => ({ column, printed: true })),
    ...FOOTER_COLUMNS.filter((column) => !selected.includes(column)).map((column) => ({ column, printed: false })),
  ]
}

function moved(columns: FooterColumn[], index: number, by: -1 | 1): FooterColumn[] {
  const next = [...columns]
  const [column] = next.splice(index, 1)
  next.splice(index + by, 0, column)
  return next
}

interface Props {
  template: DocumentTemplateRead
  canWrite: boolean
  saving: boolean
  saved: boolean
  error: string | null
  onDismissError: () => void
  onChange: (changes: DocumentTemplateUpdate) => void
}

/**
 * KAN-206 — the template's layout: three choices for the letterhead, the
 * footer's master-data columns (which, in what order — the company column
 * always, A-27), and two switches. Each change is sent at once; the controls
 * wait while it saves.
 */
export function TemplateLayoutSection({ template, canWrite, saving, saved, error, onDismissError, onChange }: Props) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const draftHintId = useId()
  const disabled = !canWrite || saving
  const columns = template.footerColumns
  const rows = footerRows(columns)

  const toggleColumn = (column: FooterColumn, printed: boolean) =>
    onChange({ footerColumns: printed ? [...columns, column] : columns.filter((c) => c !== column) })

  return (
    <Card withBorder radius="md" padding="lg" component="section" aria-label={t('letterhead.header.title')}>
      <Group gap="sm" mb="md" justify="space-between">
        <Group gap="sm">
          <LayoutTemplate size={20} />
          <Text component="h3" fw={600} fz="lg" m={0}>
            {t('letterhead.header.title')}
          </Text>
        </Group>
        {saving && (
          <Text c="dimmed" fz="sm" role="status">
            {t('letterhead.saving')}
          </Text>
        )}
        {!saving && saved && (
          <Text c="dimmed" fz="sm" role="status">
            {t('letterhead.saved')}
          </Text>
        )}
      </Group>
      <Stack gap="md">
        <Input.Wrapper {...hint(t('letterhead.header.layout'), t('letterhead.header.layoutHint'))}>
          <SegmentedControl
            fullWidth
            aria-label={t('letterhead.header.layout')}
            disabled={disabled}
            value={template.headerLayout}
            onChange={(value) => onChange({ headerLayout: value as HeaderLayout })}
            data={HEADER_LAYOUTS.map((value) => ({ value, label: t(`letterhead.header.layouts.${value}`) }))}
          />
        </Input.Wrapper>
        <Input.Wrapper {...hint(t('letterhead.header.logoSize'), t('letterhead.header.logoSizeHint'))}>
          <SegmentedControl
            fullWidth
            aria-label={t('letterhead.header.logoSize')}
            disabled={disabled}
            value={template.logoSize}
            onChange={(value) => onChange({ logoSize: value as LogoSize })}
            data={LOGO_SIZES.map((value) => ({ value, label: t(`letterhead.header.logoSizes.${value}`) }))}
          />
        </Input.Wrapper>
        <Input.Wrapper {...hint(t('letterhead.header.addressWindow'), t('letterhead.header.addressWindowHint'))}>
          <SegmentedControl
            fullWidth
            aria-label={t('letterhead.header.addressWindow')}
            disabled={disabled}
            value={template.addressWindow}
            onChange={(value) => onChange({ addressWindow: value as AddressWindow })}
            data={ADDRESS_WINDOWS.map((value) => ({ value, label: t(`letterhead.header.addressWindows.${value}`) }))}
          />
        </Input.Wrapper>

        <Stack gap="xs" role="group" aria-label={t('letterhead.footer.title')}>
          <Text fw={600} fz="sm">
            {t('letterhead.footer.title')}
          </Text>
          <Text c="dimmed" fz="xs">
            {t('letterhead.footer.explanation')}
          </Text>
          {rows.map(({ column, printed }, index) => {
            const label = t(`letterhead.footer.columns.${column}`)
            const fixed = column === 'company'
            return (
              <Group key={column} justify="space-between" wrap="nowrap">
                <Checkbox
                  label={label}
                  aria-label={t('letterhead.footer.show', { column: label })}
                  checked={printed}
                  disabled={disabled || fixed}
                  onChange={(event) => toggleColumn(column, event.currentTarget.checked)}
                />
                {printed && (
                  <Group gap={4} wrap="nowrap">
                    <ActionIcon
                      variant="subtle"
                      aria-label={t('letterhead.footer.moveUp', { column: label })}
                      disabled={disabled || index === 0}
                      onClick={() => onChange({ footerColumns: moved(columns, index, -1) })}
                    >
                      <ArrowLeft size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      aria-label={t('letterhead.footer.moveDown', { column: label })}
                      disabled={disabled || index === columns.length - 1}
                      onClick={() => onChange({ footerColumns: moved(columns, index, 1) })}
                    >
                      <ArrowRight size={16} />
                    </ActionIcon>
                  </Group>
                )}
              </Group>
            )
          })}
          <Text c="dimmed" fz="xs">
            {t('letterhead.footer.companyFixed')}
          </Text>
        </Stack>

        <Stack gap="xs" role="group" aria-label={t('letterhead.options.title')}>
          <Text fw={600} fz="sm">
            {t('letterhead.options.title')}
          </Text>
          <Switch
            {...hint(t('letterhead.options.draftWatermark'), t('letterhead.options.draftWatermarkHint'), {
              descriptionId: draftHintId,
            })}
            checked={template.draftWatermark}
            disabled={disabled}
            onChange={(event) => onChange({ draftWatermark: event.currentTarget.checked })}
          />
          <Switch
            label={t('letterhead.options.pageNumbers')}
            checked={template.pageNumbers}
            disabled={disabled}
            onChange={(event) => onChange({ pageNumbers: event.currentTarget.checked })}
          />
        </Stack>
      </Stack>
      {error && (
        <Alert color="red" role="alert" mt="md" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={onDismissError}>
          {error}
        </Alert>
      )}
    </Card>
  )
}
