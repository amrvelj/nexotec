import { useEffect, useState } from 'react'
import { Select, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { StockItemCondition, StockItemRead } from '../../api/types'
import { translatedStockConditionOptions } from '../../stockOptions'
import { apiRefusalText } from '../../utils/apiRefusal'

interface ChangeConditionDialogProps {
  opened: boolean
  onClose: () => void
  item: Pick<StockItemRead, 'id' | 'version' | 'stockNumber' | 'condition'>
  onChanged: (updated: StockItemRead) => void
}

/**
 * KAN-571 (Anto, 2026-11-07, 2b option A) — «Zustand ändern»: the only place
 * the SPA calls `POST /v1/inventory/stock-items/{id}/condition`
 * (`stock_condition`, If-Match; the server audits the change). The user
 * picks the new condition (Neu, Occasion, Vorführwagen, Tageszulassung); the
 * current one is preselected and saving it unchanged is held back. A refusal
 * (a stale version among them) is said in the UI language.
 */
export function ChangeConditionDialog({ opened, onClose, item, onChanged }: ChangeConditionDialogProps) {
  const { t } = useTranslation()
  const [condition, setCondition] = useState<StockItemCondition>(item.condition)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (opened) {
      setCondition(item.condition)
      setSubmitError(null)
    }
  }, [opened, item.condition])

  const submit = async () => {
    if (condition === item.condition) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const updated = await api.post<StockItemRead>(
        `/inventory/stock-items/${item.id}/condition`,
        { condition },
        { 'If-Match': String(item.version) },
      )
      onChanged(updated)
      onClose()
    } catch (err) {
      setSubmitError(apiRefusalText(t, err, 'stockDetail.condition.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={t('stockDetail.condition.title')}
      onSubmit={submit}
      submitLabel={t('stockDetail.condition.submit')}
      cancelLabel={t('stockDetail.condition.cancel')}
      submitting={submitting}
      submitDisabled={condition === item.condition}
    >
      <Stack gap="sm">
        <Text size="sm">{t('stockDetail.condition.body', { stockNumber: item.stockNumber })}</Text>
        <Select
          label={t('stockDetail.condition.label')}
          data={translatedStockConditionOptions(t)}
          value={condition}
          onChange={(value) => value && setCondition(value as StockItemCondition)}
          allowDeselect={false}
          withAsterisk
          data-autofocus
        />
        {submitError && (
          <Text size="xs" c="red" role="alert">
            {submitError}
          </Text>
        )}
      </Stack>
    </FormDialog>
  )
}
