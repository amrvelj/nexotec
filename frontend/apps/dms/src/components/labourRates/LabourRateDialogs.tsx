import { useState } from 'react'
import { Alert, Button, Group, Modal, SegmentedControl, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog, Picker, type PickerRow } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import type { ContactPage, ContactRead, LabourRateCategoryRead, LocationRead } from '../../api/types'
import { apiRefusalText } from '../../utils/apiRefusal'

const LANGUAGES = ['de', 'fr', 'it', 'en'] as const

/** A JS number from ChfInput as the decimal string the API takes (ADR-217). */
function decimalString(value: number | string): string | null {
  const number = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(number) || value === '') return null
  return number.toFixed(2)
}

export type WithdrawTarget = { kind: 'rate' | 'agreement'; id: string; version: number }

function useSubmit(fallbackKey: string) {
  const { t } = useTranslation()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (call: () => Promise<void>) => {
    setSubmitting(true)
    setError(null)
    try {
      await call()
    } catch (err) {
      setError(apiRefusalText(t, err, fallbackKey))
    } finally {
      setSubmitting(false)
    }
  }
  return { submitting, error, submit }
}

export function CategoryDialog({
  category,
  onClose,
  onSave,
}: {
  category: LabourRateCategoryRead | null
  onClose: () => void
  onSave: (body: { name: string; nameTranslations: Record<string, string> }) => Promise<void>
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(category?.name ?? '')
  const [translations, setTranslations] = useState<Record<string, string>>(
    (category?.nameTranslations as Record<string, string> | undefined) ?? {},
  )
  const { submitting, error, submit } = useSubmit('labourRates.saveError')
  return (
    <FormDialog
      opened
      onClose={onClose}
      title={category ? t('labourRates.categoryDialog.editTitle') : t('labourRates.categoryDialog.createTitle')}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={name.trim() === ''}
      onSubmit={() =>
        submit(() =>
          onSave({
            name: name.trim(),
            nameTranslations: Object.fromEntries(
              Object.entries(translations).filter(([, text]) => text.trim() !== ''),
            ),
          }),
        )
      }
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <TextInput
          label={t('labourRates.categoryDialog.name')}
          required
          maxLength={100}
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          data-autofocus
        />
        <Text fz="sm" c="dimmed">
          {t('labourRates.categoryDialog.translationsHint')}
        </Text>
        {LANGUAGES.map((language) => (
          <TextInput
            key={language}
            label={t('labourRates.categoryDialog.translation', { language: t(`labourRates.languages.${language}`) })}
            maxLength={100}
            value={translations[language] ?? ''}
            onChange={(event) => {
              const text = event.currentTarget.value
              setTranslations((current) => ({ ...current, [language]: text }))
            }}
          />
        ))}
      </Stack>
    </FormDialog>
  )
}

export function RateDialog({
  category,
  enteredAt,
  today,
  locations,
  onClose,
  onSave,
}: {
  category: LabourRateCategoryRead
  /** O16 at a location: its own value, else the Betrieb's (ruling 175); null while not set. */
  enteredAt: (locationId: string | null) => string | null
  /** The business's today (Europe/Zurich), the day the server checks «today or later»
   * against — not the browser's (todayLocalIsoDate), so a dealer abroad gets no refusal. */
  today: string
  locations: LocationRead[]
  onClose: () => void
  onSave: (body: { locationId: string; validFrom: string; amount: string; entered: string }) => Promise<void>
}) {
  const { t } = useTranslation()
  const [locationId, setLocationId] = useState<string | null>(locations.length === 1 ? locations[0].id : null)
  const [validFrom, setValidFrom] = useState(today)
  const [amount, setAmount] = useState<number | string>('')
  const { submitting, error, submit } = useSubmit('labourRates.saveError')
  const amountText = decimalString(amount)
  const entered = locationId ? enteredAt(locationId) : null
  return (
    <FormDialog
      opened
      onClose={onClose}
      title={t('labourRates.rateDialog.title', { name: category.name })}
      submitLabel={t('common.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={entered === null || !locationId || !validFrom || amountText === null || Number(amountText) <= 0}
      onSubmit={() =>
        submit(() =>
          onSave({ locationId: locationId ?? '', validFrom, amount: amountText ?? '', entered: entered ?? '' }),
        )
      }
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Alert color="blue" variant="light">
          {t('labourRates.rateDialog.writtenOnce')}
        </Alert>
        {locationId && entered === null && (
          <Alert color="yellow" variant="light">
            {t('labourRates.settings.enteredNotSet')}
          </Alert>
        )}
        <Select
          label={t('labourRates.columns.location')}
          required
          data={locations.map((location) => ({ value: location.id, label: location.name }))}
          value={locationId}
          onChange={setLocationId}
        />
        <TextInput
          type="date"
          label={t('labourRates.columns.validFrom')}
          required
          min={today}
          value={validFrom}
          onChange={(event) => setValidFrom(event.currentTarget.value)}
        />
        <ChfInput
          label={
            entered
              ? t('labourRates.rateDialog.amount', { entered: t(`labourRates.entered.${entered}`) })
              : t('labourRates.columns.amount')
          }
          required
          min={0}
          value={amount}
          onChange={setAmount}
        />
      </Stack>
    </FormDialog>
  )
}

export function AgreementDialog({
  categories,
  entered,
  today,
  onClose,
  onSave,
}: {
  categories: LabourRateCategoryRead[]
  /** O16 of the Betrieb: an agreement is Betrieb-wide (Anto, 2026-11-25). */
  entered: string | null
  /** The business's today, as in RateDialog. */
  today: string
  onClose: () => void
  onSave: (body: {
    contactId: string
    categoryId: string
    kind: string
    validFrom: string
    amount: string | null
    entered: string | null
  }) => Promise<void>
}) {
  const { t } = useTranslation()
  const [contact, setContact] = useState<{ id: string; label: string } | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debounced] = useDebouncedValue(query, 250)
  const search = useQuery({
    queryKey: ['contact-search', debounced],
    queryFn: () => api.get<ContactPage>(`/contacts?q=${encodeURIComponent(debounced)}&limit=10`),
    enabled: pickerOpen && debounced.length > 0,
  })
  const [categoryId, setCategoryId] = useState<string | null>(categories.length === 1 ? categories[0].id : null)
  const [kind, setKind] = useState<'rate' | 'end'>('rate')
  const [validFrom, setValidFrom] = useState(today)
  const [amount, setAmount] = useState<number | string>('')
  const { submitting, error, submit } = useSubmit('labourRates.saveError')
  const amountText = decimalString(amount)
  const ready =
    contact !== null &&
    categoryId !== null &&
    validFrom !== '' &&
    (kind === 'end' || (entered !== null && amountText !== null && Number(amountText) > 0))
  return (
    <>
      <FormDialog
        opened={!pickerOpen}
        onClose={onClose}
        title={t('labourRates.agreementDialog.title')}
        submitLabel={t('common.save')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
        submitDisabled={!ready}
        onSubmit={() =>
          submit(() =>
            onSave({
              contactId: contact?.id ?? '',
              categoryId: categoryId ?? '',
              kind,
              validFrom,
              amount: kind === 'rate' ? amountText : null,
              entered: kind === 'rate' ? entered : null,
            }),
          )
        }
      >
        <Stack gap="sm">
          {error && (
            <Alert color="red" role="alert">
              {error}
            </Alert>
          )}
          <div>
            <Text fz="sm" fw={500}>
              {t('labourRates.agreements.columns.contact')}
            </Text>
            <Group gap="xs">
              <Text fz="sm">{contact?.label ?? t('labourRates.agreementDialog.noContact')}</Text>
              <Button size="compact-xs" variant="default" onClick={() => setPickerOpen(true)}>
                {t('labourRates.agreementDialog.chooseContact')}
              </Button>
            </Group>
          </div>
          <Select
            label={t('labourRates.agreements.columns.category')}
            required
            data={categories.map((category) => ({ value: category.id, label: category.name }))}
            value={categoryId}
            onChange={setCategoryId}
          />
          <SegmentedControl
            aria-label={t('labourRates.agreementDialog.kind')}
            value={kind}
            onChange={(value) => setKind(value === 'end' ? 'end' : 'rate')}
            data={[
              { value: 'rate', label: t('labourRates.agreementDialog.kindRate') },
              { value: 'end', label: t('labourRates.agreementDialog.kindEnd') },
            ]}
          />
          <TextInput
            type="date"
            label={t('labourRates.columns.validFrom')}
            required
            min={today}
            value={validFrom}
            onChange={(event) => setValidFrom(event.currentTarget.value)}
          />
          {kind === 'rate' && (
            <ChfInput
              label={
                entered
                  ? t('labourRates.rateDialog.amount', { entered: t(`labourRates.entered.${entered}`) })
                  : t('labourRates.columns.amount')
              }
              required
              min={0}
              value={amount}
              onChange={setAmount}
            />
          )}
        </Stack>
      </FormDialog>
      <Modal opened={pickerOpen} onClose={() => setPickerOpen(false)} title={t('labourRates.agreementDialog.chooseContact')}>
        <Picker
          rows={(search.data?.items ?? []).map(
            (c: ContactRead): PickerRow => ({
              id: c.id,
              identifier: c.contactNumber,
              label: c.companyName ?? [c.firstName, c.lastName].filter(Boolean).join(' '),
            }),
          )}
          query={query}
          onQueryChange={setQuery}
          onSelect={(row) => {
            setContact({ id: row.id, label: row.label })
            setPickerOpen(false)
          }}
          loading={search.isFetching}
          placeholder={t('labourRates.agreementDialog.searchContact')}
          emptyLabel={t('common.noMatches')}
        />
      </Modal>
    </>
  )
}

export function WithdrawDialog({
  target,
  onClose,
  onConfirm,
}: {
  target: WithdrawTarget
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const { t } = useTranslation()
  const { submitting, error, submit } = useSubmit('labourRates.saveError')
  return (
    <FormDialog
      opened
      destructive
      onClose={onClose}
      title={t(target.kind === 'rate' ? 'labourRates.withdraw.rateTitle' : 'labourRates.withdraw.agreementTitle')}
      submitLabel={t('labourRates.withdraw.action')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      onSubmit={() => submit(onConfirm)}
    >
      <Stack gap="sm">
        {error && (
          <Alert color="red" role="alert">
            {error}
          </Alert>
        )}
        <Text fz="sm">
          {t(target.kind === 'rate' ? 'labourRates.withdraw.rateBody' : 'labourRates.withdraw.agreementBody')}
        </Text>
      </Stack>
    </FormDialog>
  )
}
