import { useEffect, useMemo, useState } from 'react'
import { Anchor, Checkbox, Group, NumberInput, SegmentedControl, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChfInput, FormDialog } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type {
  DealershipRead,
  LabourRateCategoryList,
  LabourRateProposalRead,
  LabourTimeUnit,
  PositionKind,
  PriceEntry,
  ServiceOrderPositionCreate,
  ServiceOrderPositionRead,
  ServiceOrderPositionUpdate,
  ServiceOrderRead,
} from '../../../../api/types'
import { useAuth } from '../../../../auth/AuthContext'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import { formatDateOnly } from '../../../../utils/format'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { decimal } from '../../serviceOrderOptions'
import { RefusalAlert } from './RefusalAlert'

interface PositionDialogProps {
  opened: boolean
  order: Pick<ServiceOrderRead, 'id' | 'version'>
  jobId: string
  /** The position to change; absent for a new one. */
  position?: ServiceOrderPositionRead | null
  onClose: () => void
  onSaved: (order: ServiceOrderRead) => void
  onReload: () => void
}

type KindTab = 'labour' | 'text'

/** A price as the advisor typed it: the amount and which of the two it is (decision 15).
 * An emptied field is `''`: the advisor is typing, nothing is proposed into it. */
interface TypedPrice {
  amount: number | ''
  entered: PriceEntry
}

const round2 = (value: number) => Math.round(value * 100) / 100

/** The other figure of a typed price, shown only (the server keeps the typed one and works out the other). */
function otherFigure(price: TypedPrice, vatRate: number | null): number | '' {
  if (vatRate === null || price.amount === '') return ''
  const factor = 1 + vatRate / 100
  return round2(price.entered === 'net' ? price.amount * factor : price.amount / factor)
}

/**
 * D13 «Position» (KAN-810 criterion 3): labour at a fixed price, labour by
 * time («Nach gestempelter Zeit verrechnen»), or text. Every rule is the
 * server's:
 *
 * - labour by time proposes the rate «which applies here» and names its source
 *   (`GET …/labour-rate-proposal`, check 11); left as proposed, no price is
 *   sent and the server takes the proposal and keeps where it came from;
 * - a fixed price is always typed (Anto, Q2): no proposal for it;
 * - the price typed last leads, net or gross (check 12): it is sent as
 *   `unitPrice` with `priceEntered`; the other field shows the other figure
 *   as a preview only, never sent;
 * - the time is typed in the unit in force at the order's location (hours
 *   or work units, O18/O19, which the proposal endpoint names); a position
 *   keeps the unit it was entered with, so a change of it is sent with that
 *   unit and the server refuses it if the unit has changed since (check 48).
 */
export function PositionDialog({ opened, order, jobId, position, onClose, onSaved, onReload }: PositionDialogProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const idempotency = useIdempotencyKey()
  const editing = Boolean(position)

  const [kindTab, setKindTab] = useState<KindTab>('labour')
  const [text, setText] = useState('')
  const [categoryId, setCategoryId] = useState<string | null>(null)
  const [byTime, setByTime] = useState(true)
  const [time, setTime] = useState<number | ''>('')
  const [typed, setTyped] = useState<TypedPrice | null>(null)
  // Editing labour by time: «Vorschlag übernehmen» sends `unitPrice: null`, the proposal again.
  const [backToProposal, setBackToProposal] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (!opened) return
    idempotency.renew()
    setError(null)
    setTyped(null)
    setBackToProposal(false)
    setKindTab(position?.kind === 'text' ? 'text' : 'labour')
    setText(position?.text ?? '')
    setCategoryId(position?.labourCategoryId ?? null)
    setByTime(position ? position.kind === 'labour_time' : true)
    setTime(position?.time ? decimal(position.time.quantity) : '')
  }, [opened, position, idempotency])

  const labour = kindTab === 'labour'
  const kind: PositionKind = !labour ? 'text' : byTime ? 'labour_time' : 'labour_fixed'

  const categories = useQuery({
    queryKey: ['dealerships', dealershipId, 'labour-rate-categories', 'active'],
    queryFn: () => api.get<LabourRateCategoryList>(`/dealerships/${dealershipId}/labour-rate-categories?includeInactive=false`),
    enabled: opened && labour && dealershipId !== '',
  })
  const proposal = useQuery({
    queryKey: ['service-order', order.id, 'labour-rate-proposal', categoryId],
    queryFn: () =>
      api.get<LabourRateProposalRead>(
        `/service-orders/${order.id}/labour-rate-proposal?${new URLSearchParams({ labourCategoryId: categoryId! }).toString()}`,
      ),
    enabled: opened && labour && categoryId !== null,
  })
  const dealership = useQuery({
    queryKey: ['dealership', dealershipId],
    queryFn: () => api.get<DealershipRead>(`/dealerships/${dealershipId}`),
    enabled: opened && labour && dealershipId !== '',
  })

  const categoryOptions = useMemo(() => {
    const language = i18n.language
    const options = (categories.data?.items ?? []).map((category) => ({
      value: category.id,
      label: (category.nameTranslations as Record<string, string>)[language] ?? category.name,
    }))
    // A position may name a category deactivated since; it stays readable.
    if (position?.labourCategoryId && !options.some((o) => o.value === position.labourCategoryId)) {
      options.push({ value: position.labourCategoryId, label: position.labourCategoryLabel ?? position.labourCategoryId })
    }
    return options
  }, [categories.data, i18n.language, position])

  // The unit: a position keeps the one it was entered with; a new one takes the unit in force here.
  const unit: LabourTimeUnit | null = position?.time?.unit ?? proposal.data?.timeUnit ?? null
  const workUnitsPerHour = position?.time ? position.time.workUnitsPerHour : (proposal.data?.workUnitsPerHour ?? null)
  const vatRate =
    position?.unitPrice?.vatRate != null
      ? decimal(position.unitPrice.vatRate)
      : proposal.data?.unitPrice?.vatRate != null
        ? decimal(proposal.data.unitPrice.vatRate)
        : dealership.data?.vatRate != null
          ? decimal(dealership.data.vatRate)
          : null

  // What the two price fields show: the typed price leads; else, by time, the proposal
  // (a new position, or «Vorschlag übernehmen»); else the position's own price as saved.
  const proposed = byTime && proposal.data?.found ? proposal.data.unitPrice : null
  const showsProposal = typed === null && byTime && (!editing || backToProposal)
  const saved = editing && !backToProposal && typed === null ? position?.unitPrice : null
  const netShown: number | '' = typed
    ? typed.entered === 'net'
      ? typed.amount
      : otherFigure(typed, vatRate)
    : saved
      ? round2(decimal(saved.net))
      : showsProposal && proposed
        ? round2(decimal(proposed.net))
        : ''
  const grossShown: number | '' = typed
    ? typed.entered === 'gross'
      ? typed.amount
      : otherFigure(typed, vatRate)
    : saved
      ? round2(decimal(saved.gross))
      : showsProposal && proposed
        ? round2(decimal(proposed.gross))
        : ''

  const typePrice = (entered: PriceEntry) => (value: number | string) => {
    setBackToProposal(false)
    setTyped({ amount: typeof value === 'number' ? value : '', entered })
  }

  // Mirrors of the server's own shape checks (`service_order_position_incomplete`), so the
  // button waits for what the server would refuse; the server still decides. An emptied
  // price field waits for a price («Vorschlag übernehmen» brings the proposal back).
  const priceKnown =
    typed !== null
      ? typed.amount !== ''
      : (editing && !backToProposal && position?.unitPrice != null) || (byTime && proposal.data?.found === true)
  const complete =
    text.trim() !== '' &&
    (!labour || (categoryId !== null && priceKnown && (!byTime || time !== '') && (time === '' || unit !== null)))

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      let updated: ServiceOrderRead
      const headers = { 'If-Match': String(order.version) }
      if (!position) {
        const body: ServiceOrderPositionCreate = { kind, text: text.trim() }
        if (labour) {
          body.labourCategoryId = categoryId
          if (time !== '' && unit) {
            body.time = String(time)
            body.timeUnit = unit
          }
          if (typed && typed.amount !== '') {
            body.unitPrice = String(typed.amount)
            body.priceEntered = typed.entered
          }
        }
        const path = `/service-orders/${order.id}/jobs/${jobId}/positions`
        updated = await api.post<ServiceOrderRead>(path, body, { ...headers, ...idempotency.headers([path, body]) })
        idempotency.renew()
      } else {
        const body: ServiceOrderPositionUpdate = {}
        if (text.trim() !== position.text) body.text = text.trim()
        if (labour) {
          if (categoryId !== position.labourCategoryId) body.labourCategoryId = categoryId
          const before = position.time ? decimal(position.time.quantity) : ''
          if (time !== before) {
            body.time = time === '' ? null : String(time)
            if (time !== '' && unit) body.timeUnit = unit
          }
          if (typed && typed.amount !== '') {
            body.unitPrice = String(typed.amount)
            body.priceEntered = typed.entered
          } else if (backToProposal) {
            body.unitPrice = null
          }
        }
        updated = await api.patch<ServiceOrderRead>(`/service-orders/${order.id}/positions/${position.id}`, body, headers)
      }
      onSaved(updated)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  const proposalLine = () => {
    if (!byTime || categoryId === null) return null
    if (proposal.isError) {
      return (
        <Text size="xs" c="red">
          {t('serviceOrder.positionDialog.proposalError')}
        </Text>
      )
    }
    if (!proposal.data) return null
    if (!proposal.data.found || !proposal.data.source) {
      return (
        <Text size="xs" c="dimmed" data-testid="rate-proposal">
          {t('serviceOrder.positionDialog.noProposal')}
        </Text>
      )
    }
    const source = t(`serviceOrder.rateSource.${proposal.data.source}`)
    return (
      <Group gap="xs">
        <Text size="xs" c="dimmed" data-testid="rate-proposal">
          {proposal.data.validFrom
            ? t('serviceOrder.positionDialog.proposalFrom', { source, date: formatDateOnly(proposal.data.validFrom, locale) })
            : t('serviceOrder.positionDialog.proposal', { source })}
        </Text>
        {(typed !== null || (editing && position?.rateSource === 'typed' && !backToProposal)) && (
          <Anchor
            component="button"
            type="button"
            size="xs"
            onClick={() => {
              setTyped(null)
              setBackToProposal(editing)
            }}
          >
            {t('serviceOrder.positionDialog.useProposal')}
          </Anchor>
        )}
      </Group>
    )
  }

  const unitShort = unit ? t(`serviceOrder.timeUnit.${unit}`) : null
  // A labour time in hours or work units, never money (no-bare-money-number-input.test.ts).
  const labourTimeLabel = unitShort
    ? t('serviceOrder.positionDialog.quantityIn', { unit: unitShort })
    : t('serviceOrder.positionDialog.quantity')

  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={editing ? t('serviceOrder.positionDialog.editTitle') : t('serviceOrder.positionDialog.addTitle')}
      onSubmit={submit}
      submitLabel={t('serviceOrder.positionDialog.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
      submitDisabled={!complete}
      size="lg"
    >
      {!editing && (
        <SegmentedControl
          aria-label={t('serviceOrder.positionDialog.kindLabel')}
          value={kindTab}
          onChange={(value) => setKindTab(value as KindTab)}
          data={[
            { value: 'labour', label: t('serviceOrder.positionDialog.kinds.labour') },
            { value: 'text', label: t('serviceOrder.positionDialog.kinds.text') },
          ]}
          style={{ alignSelf: 'flex-start' }}
        />
      )}
      <TextInput
        label={t('serviceOrder.positionDialog.text')}
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
        maxLength={1000}
        required
        data-autofocus
      />
      {labour && (
        <Stack gap="sm">
          <Select
            label={t('serviceOrder.positionDialog.labourCategory')}
            data={categoryOptions}
            value={categoryId}
            onChange={(value) => {
              setCategoryId(value)
              setTyped(null)
              setBackToProposal(false)
            }}
            required
            allowDeselect={false}
            searchable
            error={categories.isError ? t('serviceOrder.positionDialog.categoriesError') : undefined}
          />
          {categories.isSuccess && categoryOptions.length === 0 && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.noCategories')}
            </Text>
          )}
          <Checkbox
            label={t('serviceOrder.positionDialog.byTime')}
            checked={byTime}
            disabled={editing}
            onChange={(e) => {
              setByTime(e.currentTarget.checked)
              setTyped(null)
            }}
          />
          <SimpleGrid cols={3} spacing="sm">
            <NumberInput
              label={labourTimeLabel}
              value={time}
              onChange={(value) => setTime(typeof value === 'number' ? value : '')}
              min={0}
              decimalScale={2}
              decimalSeparator="."
              thousandSeparator="'"
              disabled={unit === null}
              required={byTime}
            />
            <ChfInput
              label={byTime ? t('serviceOrder.positionDialog.rateNet') : t('serviceOrder.positionDialog.fixedNet')}
              value={netShown}
              onChange={typePrice('net')}
              min={0}
            />
            <ChfInput
              label={byTime ? t('serviceOrder.positionDialog.rateGross') : t('serviceOrder.positionDialog.fixedGross')}
              value={grossShown}
              onChange={typePrice('gross')}
              min={0}
            />
          </SimpleGrid>
          {unit === null && categoryId === null && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.chooseCategoryFirst')}
            </Text>
          )}
          {unit === 'work_units' && workUnitsPerHour && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.workUnitsPerHour', { count: decimal(workUnitsPerHour) })}
            </Text>
          )}
          {editing && position?.time && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.keepsUnit', { unit: t(`serviceOrder.unitName.${position.time.unit}`) })}
            </Text>
          )}
          {proposalLine()}
          {!byTime && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.fixedTyped')} {t('serviceOrder.positionDialog.quantityHint')}
            </Text>
          )}
          <Text size="xs" c="dimmed">
            {t('serviceOrder.positionDialog.priceLeads')}
          </Text>
        </Stack>
      )}
      {error !== null && (
        <RefusalAlert
          error={error}
          fallbackKey="serviceOrder.positionDialog.error"
          onReload={() => {
            onReload()
            onClose()
          }}
        />
      )}
    </FormDialog>
  )
}
