import { Select } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { useAdvisorOptions } from '../../hooks/useAdvisorOptions'

export interface DealOwnerSelectProps {
  deal: { ownerUserId?: string | null; ownerLabel?: string | null }
  /** Sends `ownerUserId` (null = «Nicht zugewiesen») through the host's own
   * PATCH with If-Match; a refusal is the host's to say, so a rejected
   * promise is swallowed here. */
  onChange: (ownerUserId: string | null) => Promise<unknown>
  disabled?: boolean
  /** ADR-061: why the seller cannot change here (KAN-492: the offer's live
   * contract carries it). Disables the picker and says so beneath it. */
  disabledReason?: string
}

/**
 * KAN-405 — the responsible seller of an offer or a pending contract
 * (KAN-200's `ownerUserId`). The choices are the acting Betrieb's active
 * users and members (`GET /customers/advisor-options`, the rule
 * `resolve_owner_change` applies on the server); a seller who is no longer
 * among them stays shown by name, as the customer's advisor does.
 */
export function DealOwnerSelect({ deal, onChange, disabled = false, disabledReason }: DealOwnerSelectProps) {
  const { t } = useTranslation()
  const { options, isLoading } = useAdvisorOptions()
  const current = deal.ownerUserId ?? null
  const data =
    current && !options.some((o) => o.value === current)
      ? [...options, { value: current, label: deal.ownerLabel ?? current }]
      : options

  return (
    <Select
      label={t('dealOwner.label')}
      placeholder={t('dealOwner.unassigned')}
      data={data}
      value={current}
      onChange={(value) => {
        if (value === current) return
        onChange(value).catch(() => {})
      }}
      clearable
      searchable
      nothingFoundMessage={t('common.noMatches')}
      disabled={disabled || disabledReason !== undefined || isLoading}
      // A disabled reason (ADR-061), not a field hint (KAN-443): said under
      // the input and linked to it by Mantine's aria-describedby.
      description={disabledReason}
      inputWrapperOrder={['label', 'input', 'description']}
      maw={360}
    />
  )
}
