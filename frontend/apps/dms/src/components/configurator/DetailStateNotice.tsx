import { Alert, Text } from '@mantine/core'
import { Info, Plug, Unplug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DETAIL_NOTE_I18N_KEY, hasNoteToShow, type CatalogueDetailName, type CatalogueDetailStateRead } from './detailState'

export interface DetailStateNoticeProps {
  detail: CatalogueDetailName
  state: CatalogueDetailStateRead
}

/**
 * KAN-211 / KAN-212 (ADR-123) — how a catalogue detail's load state reads on
 * screen, the same on every tab that shows a detail: «Nicht in Ihrem
 * Vertrag» when the provider's licence refused it, «derzeit nicht verfügbar»
 * when the request failed (the next open asks again), «noch nicht geladen»
 * when this Betrieb never asked. A loaded detail renders nothing — unless
 * its note says the provider answered a coherent «nothing» (a key it no
 * longer lists), which reads as an information line. Every note is
 * translated or left out: a failure's class name is for the call log.
 */
export function DetailStateNotice({ detail, state }: DetailStateNoticeProps) {
  const { t } = useTranslation()
  const noteKey = state.note != null ? DETAIL_NOTE_I18N_KEY[state.note] : undefined

  if (state.state === 'present') {
    if (!hasNoteToShow(state) || !noteKey) return null
    return (
      <Alert color="gray" icon={<Info size={16} />} data-testid={`detail-state-${detail}`} data-state="present">
        <Text size="sm">{t(noteKey)}</Text>
      </Alert>
    )
  }

  const icon = state.state === 'notInContract' ? <Plug size={16} /> : state.state === 'unavailable' ? <Unplug size={16} /> : <Info size={16} />
  const color = state.state === 'notInContract' ? 'yellow' : state.state === 'unavailable' ? 'orange' : 'gray'
  return (
    <Alert
      color={color}
      icon={icon}
      title={t(`configurator.details.${state.state}.title`)}
      data-testid={`detail-state-${detail}`}
      data-state={state.state}
    >
      <Text size="sm">{t(`configurator.details.${state.state}.body`)}</Text>
      {noteKey && (
        <Text size="xs" c="dimmed" mt={4}>
          {t(noteKey)}
        </Text>
      )}
    </Alert>
  )
}
