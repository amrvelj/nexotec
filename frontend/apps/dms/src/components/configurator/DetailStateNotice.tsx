import { Alert, Text } from '@mantine/core'
import { Info, Plug, Unplug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatDateTime } from '../../utils/format'
import type { CatalogueSpecificationRead } from '../../api/types'

export type CatalogueDetailName = keyof CatalogueSpecificationRead['details']
export type CatalogueDetailStateRead = CatalogueSpecificationRead['details'][CatalogueDetailName]

/** The key under `configurator.details.state` for a state value. */
export function detailStateLabelKey(state: CatalogueDetailStateRead['state']): string {
  return `configurator.details.state.${state}`
}

export interface DetailStateNoticeProps {
  detail: CatalogueDetailName
  state: CatalogueDetailStateRead
  /** `true` renders «Geladen am …» for a loaded detail too; by default a
   * loaded detail shows nothing — the data speaks for itself. */
  showLoadedAt?: boolean
}

/**
 * KAN-211 / KAN-212 (ADR-123) — how a catalogue detail's load state reads on
 * screen, the same on every tab that shows a detail: «Nicht in Ihrem
 * Vertrag» when the provider's licence refused it, «derzeit nicht verfügbar»
 * when the request failed (the next open asks again), «noch nicht geladen»
 * when this Betrieb never asked. A loaded detail renders nothing unless
 * asked for its timestamp. The provider's note (the error class, a vanished
 * key) is a dimmed line for support, never the message itself.
 */
export function DetailStateNotice({ detail, state, showLoadedAt = false }: DetailStateNoticeProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)

  if (state.state === 'present') {
    if (!showLoadedAt || !state.loadedAt) return null
    return (
      <Text size="xs" c="dimmed" data-testid={`detail-state-${detail}`}>
        {t('configurator.details.loadedAt', { date: formatDateTime(state.loadedAt, locale) })}
      </Text>
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
      {state.note && (
        <Text size="xs" c="dimmed" mt={4} ff="monospace">
          {state.note}
        </Text>
      )}
    </Alert>
  )
}
