import { Stack, Tabs, Text, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'

const PROFILE_LANGUAGES = ['de', 'fr', 'it', 'en'] as const
export type ProfileLanguage = (typeof PROFILE_LANGUAGES)[number]
export type LocalisedText = Partial<Record<ProfileLanguage, string>>

/**
 * KAN-225 — one editorial text in the four languages (D-A-26: all four ship
 * at launch). A tab per language, marked when it holds text; an empty
 * language is left out of what is saved, so a location's override inherits
 * it again. `inherited` shows the Betrieb's text a location falls back to.
 */
export function LocalisedTextarea({
  label,
  value,
  onChange,
  inherited,
  readOnly = false,
  minRows = 2,
  maxLength,
}: {
  label: string
  value: LocalisedText
  onChange: (next: LocalisedText) => void
  inherited?: LocalisedText
  readOnly?: boolean
  minRows?: number
  maxLength?: number
}) {
  const { t } = useTranslation()
  return (
    <Stack gap={4} role="group" aria-label={label}>
      <Text fw={500} fz="sm">
        {label}
      </Text>
      <Tabs defaultValue="de" keepMounted={false}>
        <Tabs.List>
          {PROFILE_LANGUAGES.map((language) => (
            <Tabs.Tab key={language} value={language}>
              {t(`publishing.languages.${language}`)}
              {value[language] ? ' •' : ''}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {PROFILE_LANGUAGES.map((language) => (
          <Tabs.Panel key={language} value={language} pt="xs">
            <Textarea
              aria-label={`${label} (${t(`publishing.languages.${language}`)})`}
              value={value[language] ?? ''}
              placeholder={inherited?.[language] ? t('publishing.inheritsPlaceholder', { text: inherited[language] }) : undefined}
              autosize
              minRows={minRows}
              maxLength={maxLength}
              readOnly={readOnly}
              onChange={(event) => {
                const next = { ...value }
                if (event.currentTarget.value === '') delete next[language]
                else next[language] = event.currentTarget.value
                onChange(next)
              }}
            />
          </Tabs.Panel>
        ))}
      </Tabs>
    </Stack>
  )
}
