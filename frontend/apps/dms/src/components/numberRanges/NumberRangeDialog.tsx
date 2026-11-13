import { useEffect, useState } from 'react'
import { Alert, Checkbox, NumberInput, Stack, Text, TextInput, Textarea } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { FormDialog } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { NumberRangePreviewRead, NumberRangeRead } from '../../api/types'
import { useFieldHint } from '../../hooks/useFieldHint'
import { apiRefusalText } from '../../utils/apiRefusal'

export interface NumberRangeChange {
  pattern: string
  startValue: number
  yearlyReset: boolean
  reason?: string
}

const PREVIEW_DELAY_MS = 300

/**
 * KAN-207 — edit one number range: pattern, start value, yearly reset, with
 * the next number as a live example from the server (the same rules a save
 * applies, so a pattern the save would refuse says why before the save).
 * A frozen range (A-RULE-3) reaches this dialog only for platform staff, who
 * must give a reason; the dialog says the range is frozen before anything.
 */
export function NumberRangeDialog({
  dealershipId,
  range,
  onClose,
  onSave,
}: {
  dealershipId: string
  range: NumberRangeRead | null
  onClose: () => void
  onSave: (range: NumberRangeRead, change: NumberRangeChange) => Promise<void>
}) {
  const { t } = useTranslation()
  const hint = useFieldHint()
  const [pattern, setPattern] = useState('')
  const [startValue, setStartValue] = useState<number>(1)
  const [yearlyReset, setYearlyReset] = useState(false)
  const [reason, setReason] = useState('')
  const [example, setExample] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!range) return
    setPattern(range.pattern)
    setStartValue(range.startValue)
    setYearlyReset(range.yearlyReset)
    setReason('')
    setExample(range.nextNumber)
    setPreviewError(null)
    setError(null)
  }, [range])

  useEffect(() => {
    if (!range || pattern.trim() === '') return
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .post<NumberRangePreviewRead>(`/dealerships/${dealershipId}/number-range-previews`, {
          documentType: range.documentType,
          pattern: pattern.trim(),
          startValue,
          yearlyReset,
        })
        .then((preview) => {
          if (cancelled) return
          setExample(preview.nextNumber)
          setPreviewError(null)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setExample(null)
          setPreviewError(apiRefusalText(t, err, 'numberRanges.previewError'))
        })
    }, PREVIEW_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [dealershipId, range, pattern, startValue, yearlyReset, t])

  const frozen = range?.frozenAt != null
  const valid = pattern.trim() !== '' && startValue >= 1 && previewError === null && (!frozen || reason.trim() !== '')

  return (
    <FormDialog
      opened={range !== null}
      onClose={onClose}
      title={range ? t('numberRanges.editTitle', { type: t(`numberRanges.documentType.${range.documentType}`) }) : ''}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!valid}
      onSubmit={async () => {
        if (!range || !valid) return
        setSubmitting(true)
        setError(null)
        try {
          await onSave(range, { pattern: pattern.trim(), startValue, yearlyReset, ...(frozen ? { reason: reason.trim() } : {}) })
          onClose()
        } catch (err) {
          setError(apiRefusalText(t, err, 'numberRanges.saveError'))
        } finally {
          setSubmitting(false)
        }
      }}
    >
      <Stack gap="sm">
        {frozen && (
          <Alert color="orange" role="status">
            {t('numberRanges.frozenStaffNotice')}
          </Alert>
        )}
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <TextInput
          {...hint(t('numberRanges.pattern'), t('numberRanges.patternHint'))}
          value={pattern}
          onChange={(e) => setPattern(e.currentTarget.value)}
          required
        />
        <NumberInput
          label={t('numberRanges.startValue')}
          value={startValue}
          min={1}
          allowDecimal={false}
          onChange={(value) => setStartValue(typeof value === 'number' ? value : Number(value) || 1)}
          required
        />
        <Checkbox
          label={t('numberRanges.yearlyReset')}
          checked={yearlyReset}
          onChange={(e) => setYearlyReset(e.currentTarget.checked)}
        />
        {frozen && (
          <Textarea
            label={t('numberRanges.reason')}
            value={reason}
            onChange={(e) => setReason(e.currentTarget.value)}
            required
            autosize
            minRows={2}
          />
        )}
        <div aria-live="polite">
          {previewError ? (
            <Text size="sm" c="red">
              {previewError}
            </Text>
          ) : (
            <Text size="sm">
              {t('numberRanges.example')}: <strong>{example ?? '—'}</strong>
            </Text>
          )}
        </div>
      </Stack>
    </FormDialog>
  )
}
