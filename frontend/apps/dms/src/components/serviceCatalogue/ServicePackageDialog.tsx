import { useId, useState } from 'react'
import {
  ActionIcon,
  Alert,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import type { LabourRateCategoryRead, ServicePackageRead, ServicePackageWrite } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const

/** O18/O19 for the Betrieb (ruling 183): hours, or work units with how many make an hour. */
export interface TimeUnitInForce {
  unit: 'hours' | 'work_units'
  workUnitsPerHour: string | null
}

interface Row {
  key: string
  id: string | null
  kind: 'labour' | 'text'
  text: string
  labourCategoryId: string | null
  time: number | string
  /** The unit the stored time was typed in; a new time is typed in the unit in force. */
  timeUnit: 'hours' | 'work_units' | null
  checkNeeded: boolean
}

let rowCounter = 0
const nextKey = () => `row-${(rowCounter += 1)}`

function rowsOf(pkg: ServicePackageRead | null): Row[] {
  return (pkg?.positions ?? []).map((position) => ({
    key: nextKey(),
    id: position.id,
    kind: position.kind,
    text: position.text,
    labourCategoryId: position.labourCategoryId,
    time: position.time ? Number(position.time.quantity) : '',
    timeUnit: position.time?.unit ?? null,
    checkNeeded: position.checkNeeded,
  }))
}

/** A JS number from NumberInput as the decimal string the API takes (ADR-217); null when empty. */
function timeString(value: number | string): string | null {
  if (value === '') return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : null
}

/**
 * KAN-818 — a service package (section 3.10): its name, typed once, with an
 * optional name per language (ruling 176); its labour positions with a fixed
 * time in the Betrieb's unit, a labour category and «Prüfung nötig»; its text
 * positions; active or inactive. The whole package is saved at once (PUT
 * with If-Match); the API is the authority on every rule and its refusal
 * shows in the UI language.
 */
export function ServicePackageDialog({
  pkg,
  categories,
  unitInForce,
  onClose,
  onSave,
}: {
  pkg: ServicePackageRead | null
  categories: LabourRateCategoryRead[]
  unitInForce: TimeUnitInForce
  onClose: () => void
  onSave: (body: ServicePackageWrite) => Promise<void>
}) {
  const { t, i18n } = useTranslation()
  const [name, setName] = useState(pkg?.name ?? '')
  const [translations, setTranslations] = useState<Record<string, string>>({ ...(pkg?.nameTranslations ?? {}) })
  const [isActive, setIsActive] = useState(pkg?.isActive ?? true)
  const [rows, setRows] = useState<Row[]>(() => rowsOf(pkg))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The submit is never disabled (UI/UX Specification, Forms): it says what is missing instead.
  const [showMissing, setShowMissing] = useState(false)
  const hint = useFieldHint()
  const activeHintId = useId()
  const checkHintId = useId()

  const unitLabel = (unit: 'hours' | 'work_units') => t(`serviceCatalogue.units.${unit}`)
  const categoryName = (category: LabourRateCategoryRead) => {
    const translated = (category.nameTranslations as Record<string, string> | null)?.[i18n.language.slice(0, 2)]
    return translated && translated.trim() !== '' ? translated : category.name
  }
  const categoryOptions = categories
    .filter((category) => category.isActive || rows.some((row) => row.labourCategoryId === category.id))
    .map((category) => ({ value: category.id, label: categoryName(category) }))

  const update = (key: string, change: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...change } : row)))
  const move = (index: number, by: -1 | 1) =>
    setRows((current) => {
      const next = [...current]
      const [row] = next.splice(index, 1)
      next.splice(index + by, 0, row)
      return next
    })
  const add = (kind: Row['kind']) =>
    setRows((current) => [
      ...current,
      { key: nextKey(), id: null, kind, text: '', labourCategoryId: null, time: '', timeUnit: null, checkNeeded: false },
    ])

  const complete =
    name.trim() !== '' &&
    rows.every(
      (row) =>
        row.text.trim() !== '' &&
        (row.kind === 'text' || (row.labourCategoryId !== null && timeString(row.time) !== null)),
    )

  const missing = (condition: boolean, key: string) => (showMissing && condition ? t(`serviceCatalogue.missing.${key}`) : undefined)

  const save = async () => {
    if (!complete) {
      setShowMissing(true)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await onSave({
        name: name.trim(),
        nameTranslations: Object.fromEntries(Object.entries(translations).filter(([, text]) => text.trim() !== '')),
        isActive,
        positions: rows.map((row) =>
          row.kind === 'text'
            ? { id: row.id, kind: 'text', text: row.text.trim(), checkNeeded: false }
            : {
                id: row.id,
                kind: 'labour',
                text: row.text.trim(),
                labourCategoryId: row.labourCategoryId,
                time: timeString(row.time),
                timeUnit: row.timeUnit ?? unitInForce.unit,
                checkNeeded: row.checkNeeded,
              },
        ),
      })
    } catch (err) {
      setError(apiRefusalText(t, err, 'serviceCatalogue.saveError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened
      size="lg"
      onClose={onClose}
      title={pkg ? t('serviceCatalogue.dialog.editTitle') : t('serviceCatalogue.dialog.createTitle')}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => void save()}
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <TextInput
          label={t('serviceCatalogue.dialog.name')}
          required
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          error={missing(name.trim() === '', 'name')}
          data-autofocus
        />
        <Text fz="sm" c="dimmed">
          {t('serviceCatalogue.dialog.translationsHint')}
        </Text>
        <Group grow align="flex-start">
          {LANGUAGES.map((language) => (
            <TextInput
              key={language}
              label={t('serviceCatalogue.dialog.translation', { language: t(`serviceCatalogue.languages.${language}`) })}
              maxLength={200}
              value={translations[language] ?? ''}
              onChange={(event) => {
                const text = event.currentTarget.value
                setTranslations((current) => ({ ...current, [language]: text }))
              }}
            />
          ))}
        </Group>
        <Switch
          {...hint(t('serviceCatalogue.dialog.active'), t('serviceCatalogue.dialog.activeHint'), {
            descriptionId: activeHintId,
          })}
          checked={isActive}
          onChange={(event) => setIsActive(event.currentTarget.checked)}
        />

        <Group justify="space-between" mt="xs">
          <Title order={5}>{t('serviceCatalogue.dialog.positions')}</Title>
          <Text fz="xs" c="dimmed">
            {t('serviceCatalogue.dialog.unitNote', { unit: unitLabel(unitInForce.unit) })}
          </Text>
        </Group>
        {rows.length === 0 && (
          <Text fz="sm" c="dimmed">
            {t('serviceCatalogue.dialog.noPositions')}
          </Text>
        )}
        {rows.map((row, index) => {
          const unit = row.timeUnit ?? unitInForce.unit
          const position = index + 1
          return (
            <Paper key={row.key} withBorder radius="md" p="sm" aria-label={t('serviceCatalogue.dialog.positionLabel', { position })}>
              <Stack gap="xs">
                <Group justify="space-between" wrap="nowrap">
                  <Text fz="sm" fw={600}>
                    {t(`serviceCatalogue.kinds.${row.kind}`)}
                  </Text>
                  <Group gap={4} wrap="nowrap">
                    <ActionIcon
                      variant="subtle"
                      disabled={index === 0}
                      aria-label={t('serviceCatalogue.dialog.moveUp', { position })}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      disabled={index === rows.length - 1}
                      aria-label={t('serviceCatalogue.dialog.moveDown', { position })}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      aria-label={t('serviceCatalogue.dialog.remove', { position })}
                      onClick={() => setRows((current) => current.filter((other) => other.key !== row.key))}
                    >
                      <Trash2 size={16} />
                    </ActionIcon>
                  </Group>
                </Group>
                <TextInput
                  label={t('serviceCatalogue.dialog.text')}
                  required
                  maxLength={1000}
                  value={row.text}
                  onChange={(event) => update(row.key, { text: event.currentTarget.value })}
                  error={missing(row.text.trim() === '', 'text')}
                />
                {row.kind === 'labour' && (
                  <Group grow align="flex-start">
                    <Select
                      label={t('serviceCatalogue.dialog.category')}
                      required
                      data={categoryOptions}
                      value={row.labourCategoryId}
                      onChange={(value) => update(row.key, { labourCategoryId: value })}
                      error={missing(row.labourCategoryId === null, 'category')}
                      nothingFoundMessage={t('serviceCatalogue.dialog.noCategories')}
                    />
                    <NumberInput
                      label={t('serviceCatalogue.dialog.time')}
                      required
                      min={0.01}
                      decimalScale={2}
                      allowNegative={false}
                      rightSection={<Text fz="xs">{t(`serviceCatalogue.unitShort.${unit}`)}</Text>}
                      rightSectionWidth={40}
                      value={row.time}
                      // A changed time is typed in the unit in force (ruling 183); the API refuses another.
                      onChange={(value) => update(row.key, { time: value, timeUnit: null })}
                      error={missing(timeString(row.time) === null, 'time')}
                    />
                  </Group>
                )}
                {row.kind === 'labour' && (
                  <Checkbox
                    {...hint(t('serviceCatalogue.dialog.checkNeeded'), t('serviceCatalogue.dialog.checkNeededHint'), {
                      descriptionId: `${checkHintId}-${row.key}`,
                    })}
                    checked={row.checkNeeded}
                    onChange={(event) => update(row.key, { checkNeeded: event.currentTarget.checked })}
                  />
                )}
              </Stack>
            </Paper>
          )
        })}
        <Group gap="xs">
          <Button size="xs" variant="light" onClick={() => add('labour')}>
            {t('serviceCatalogue.dialog.addLabour')}
          </Button>
          <Button size="xs" variant="light" onClick={() => add('text')}>
            {t('serviceCatalogue.dialog.addText')}
          </Button>
        </Group>
      </Stack>
    </FormDialog>
  )
}
