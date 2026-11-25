import { useState } from 'react'
import { ActionIcon, Button, Group, NumberInput, Select, Stack, Text, TextInput, Tooltip } from '@mantine/core'
import { Pencil } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, FormDialog, InlineEditField, chfInlineEditor, slate, type EditorRenderProps } from '@nexotec/ui-kit'
import type { SettingDeclarationRead, SettingSlotRead, SettingValueBody } from '../../api/types'
import { useInlineEditRefusal } from '../../hooks/useInlineEditRefusal'
import { LANGUAGE_ENDONYMS, LANGUAGE_ORDER } from '../../i18n/languageEndonyms'
import { apiRefusalText, isStaleVersion } from '../../utils/apiRefusal'
import { SWITCH_OFF, SWITCH_ON, formatSettingValue, fromDraft, settingLabel, toDraft, type SettingKind } from './settingValue'

const CLEAR = '__clear__'

export interface SettingCellProps {
  declaration: SettingDeclarationRead
  /** This cell's own slot: the Betrieb's, or a location's. */
  slot: SettingSlotRead
  /** For a location's cell, the Betrieb's slot it inherits while it holds no value of its own. */
  inherited?: SettingSlotRead
  /** The location's name, for a location's cell. */
  locationName?: string
  canWrite: boolean
  onSave: (value: SettingValueBody | null, version: number) => Promise<void>
  onReload: () => void
}

/**
 * KAN-805 — one value of the settings screen (the ticket's Design section):
 * «not set» is a state of its own, never an empty field, zero or «off»; a
 * location without a value of its own shows the Betrieb's value, marked as
 * inherited. The manager edits inline (a text, with its translations, in a
 * dialog); every other role reads.
 */
export function SettingCell({ declaration, slot, inherited, locationName, canWrite, onSave, onReload }: SettingCellProps) {
  const { t, i18n } = useTranslation()
  const { describeError, reloadLabel } = useInlineEditRefusal('settings.saveError')
  const kind = declaration.kind as SettingKind
  const language = i18n.language
  const own = slot.isSet && slot.value ? formatSettingValue(t, language, declaration, slot.value) : null
  const isLocation = inherited !== undefined
  const label = settingLabel(t, declaration.key)

  // What an empty cell says: a location inherits the Betrieb's value (or its
  // «not set»); the Betrieb's own slot is «not set», with what that does.
  const betriebShown =
    inherited && inherited.isSet && inherited.value ? formatSettingValue(t, language, declaration, inherited.value) : null
  const emptyLabel = isLocation
    ? t('settings.inherited', { value: betriebShown ?? t('settings.notSet.label') })
    : t('settings.notSet.label')
  const notSetNote =
    !isLocation && !slot.isSet
      ? declaration.whenNotSet === 'acts_as' && declaration.actsAs
        ? t('settings.notSet.actsAs', { value: formatSettingValue(t, language, declaration, declaration.actsAs) })
        : declaration.whenNotSet === 'refuses'
          ? t('settings.notSet.refuses')
          : null
      : null
  const stale = !slot.fitsDeclaration ? (
    <Badge tone="warning">{t('settings.stale')}</Badge>
  ) : null
  const ariaScope = locationName ?? t('settings.columns.dealership')

  const display = (
    <Text span size="sm" fs={own === null ? 'italic' : undefined} c={own === null ? slate[3] : undefined}>
      {own ?? emptyLabel}
    </Text>
  )

  let field: React.ReactNode
  if (!canWrite) {
    field = display
  } else if (kind === 'text') {
    field = (
      <Group gap={4} wrap="nowrap">
        {display}
        <TextSettingEditor
          slot={slot}
          title={`${label} · ${ariaScope}`}
          clearLabel={isLocation ? t('settings.clear.location') : t('settings.clear.dealership')}
          onSave={onSave}
        />
      </Group>
    )
  } else {
    field = (
      <InlineEditField
        value={own ?? ''}
        isEmpty={own === null}
        emptyLabel={emptyLabel}
        editValue={toDraft(declaration, slot.isSet ? slot.value : null)}
        describeError={describeError}
        isConflict={isStaleVersion}
        onReload={onReload}
        reloadLabel={reloadLabel}
        renderEditor={editorFor(declaration, {
          t,
          ariaLabel: `${label} · ${ariaScope}`,
          clearLabel: isLocation ? t('settings.clear.location') : t('settings.clear.dealership'),
        })}
        onSave={(raw) => onSave(fromDraft(declaration, raw), slot.version)}
      />
    )
  }

  return (
    <Stack gap={2} align="flex-start">
      {field}
      {stale}
      {notSetNote && (
        <Text size="xs" c="dimmed">
          {notSetNote}
        </Text>
      )}
    </Stack>
  )
}

function editorFor(
  declaration: SettingDeclarationRead,
  { t, ariaLabel, clearLabel }: { t: (key: string) => string; ariaLabel: string; clearLabel: string },
): ((editor: EditorRenderProps) => React.ReactNode) | undefined {
  const kind = declaration.kind as SettingKind
  if (kind === 'amount') return chfInlineEditor({ 'aria-label': ariaLabel })
  if (kind === 'switch' || kind === 'choice') {
    const options =
      kind === 'switch'
        ? [
            { value: SWITCH_ON, label: t('settings.value.on') },
            { value: SWITCH_OFF, label: t('settings.value.off') },
          ]
        : declaration.choices.map((code) => ({ value: code, label: t(`settings.keys.${declaration.key}.options.${code}`) }))
    return function ChoiceEditor({ value, onKeyDown, onBlur, onCommit }: EditorRenderProps) {
      return (
        <Select
          size="xs"
          aria-label={ariaLabel}
          data={[...options, { value: CLEAR, label: clearLabel }]}
          value={value === '' ? null : value}
          autoFocus
          defaultDropdownOpened
          allowDeselect={false}
          onKeyDown={onKeyDown}
          onBlur={onBlur}
          onChange={(next) => onCommit(next === CLEAR || next === null ? '' : next)}
        />
      )
    }
  }
  if (kind === 'number') {
    // Not money: an amount setting renders `chfInlineEditor` above (KAN-168).
    const settingNumberLabel = ariaLabel
    return function NumberEditor({ value, onChange, onKeyDown, onBlur }: EditorRenderProps) {
      return (
        <NumberInput
          size="xs"
          aria-label={settingNumberLabel}
          value={value}
          autoFocus
          decimalScale={declaration.decimals}
          allowDecimal={declaration.decimals > 0}
          thousandSeparator="'"
          min={declaration.minimum !== null ? Number(declaration.minimum) : undefined}
          max={declaration.maximum !== null ? Number(declaration.maximum) : undefined}
          rightSection={declaration.unit ? <Text size="xs">{t(`settings.units.${declaration.unit}`)}</Text> : undefined}
          rightSectionWidth={declaration.unit ? 'auto' : undefined}
          onChange={(next) => onChange(next === '' ? '' : String(next))}
          onKeyDown={onKeyDown}
          onBlur={onBlur}
        />
      )
    }
  }
  return undefined
}

/** A text setting: one name and an optional translation per language
 * (ruling 176) — several fields, so a dialog rather than an inline field. */
function TextSettingEditor({
  slot,
  title,
  clearLabel,
  onSave,
}: {
  slot: SettingSlotRead
  title: string
  clearLabel: string
  onSave: (value: SettingValueBody | null, version: number) => Promise<void>
}) {
  const { t } = useTranslation()
  const [opened, setOpened] = useState(false)
  const [name, setName] = useState('')
  const [translations, setTranslations] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const open = () => {
    const current = slot.isSet ? slot.value?.text : null
    setName(current?.name ?? '')
    setTranslations({ ...(current?.translations ?? {}) })
    setError(null)
    setOpened(true)
  }
  const save = async (value: SettingValueBody | null) => {
    setSubmitting(true)
    setError(null)
    try {
      await onSave(value, slot.version)
      setOpened(false)
    } catch (err) {
      setError(apiRefusalText(t, err, 'settings.saveError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Tooltip label={t('settings.edit')} withArrow>
        <ActionIcon variant="subtle" size="sm" aria-label={`${t('settings.edit')}: ${title}`} onClick={open}>
          <Pencil size={14} />
        </ActionIcon>
      </Tooltip>
      <FormDialog
        opened={opened}
        onClose={() => setOpened(false)}
        title={title}
        submitLabel={t('common.save')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
        submitDisabled={name.trim() === ''}
        onSubmit={() => {
          const given = Object.fromEntries(Object.entries(translations).filter(([, text]) => text.trim() !== ''))
          void save({ text: { name: name.trim(), translations: given } })
        }}
      >
        <Stack gap="sm">
          <TextInput label={t('settings.text.name')} value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <Text size="xs" c="dimmed">
            {t('settings.text.translationsNote')}
          </Text>
          {LANGUAGE_ORDER.map((language) => (
            <TextInput
              key={language}
              label={LANGUAGE_ENDONYMS[language]}
              value={translations[language] ?? ''}
              onChange={(e) => {
                const text = e.currentTarget.value
                setTranslations((current) => ({ ...current, [language]: text }))
              }}
            />
          ))}
          {slot.isSet && (
            <Group justify="flex-start">
              <Button variant="subtle" color="red" size="xs" px={0} disabled={submitting} onClick={() => void save(null)}>
                {clearLabel}
              </Button>
            </Group>
          )}
          {error && (
            <Text size="sm" c="red" role="alert">
              {error}
            </Text>
          )}
        </Stack>
      </FormDialog>
    </>
  )
}
