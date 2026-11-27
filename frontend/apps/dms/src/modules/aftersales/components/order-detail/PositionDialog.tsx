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
  ServicePackageList,
  ServicePackageRead,
} from '../../../../api/types'
import { useAuth } from '../../../../auth/AuthContext'
import { useFieldHint } from '../../../../hooks/useFieldHint'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import { formatDateOnly } from '../../../../utils/format'
import { toSwissLocale, type SupportedLanguage } from '../../../../i18n'
import { decimal, formatLabourTime } from '../../serviceOrderOptions'
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

// KAN-818: «Aus dem Katalog» — a package from the service catalogue, copied onto the job.
type KindTab = 'labour' | 'text' | 'package'

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
 *   keeps the unit it was entered with while its time is not changed (check
 *   48); a changed time is typed and sent in the unit in force, and the dialog
 *   says so when the two differ (the server refuses any other unit,
 *   `service_order_time_unit_changed`);
 * - «Aus dem Katalog» (KAN-818, check 44) lists the active service packages,
 *   named in the UI language else as typed (ruling 176), and copies the chosen
 *   one into the job (`POST …/jobs/{jobId}/service-packages`): the server
 *   prices its labour with the rate which applies here; nothing is typed;
 * - the submit is never disabled for an incomplete form (UI/UX Specification,
 *   Forms): a submit says what is missing, field by field, and sends nothing.
 */
export function PositionDialog({ opened, order, jobId, position, onClose, onSaved, onReload }: PositionDialogProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const idempotency = useIdempotencyKey()
  const hint = useFieldHint()
  const editing = Boolean(position)

  const [kindTab, setKindTab] = useState<KindTab>('labour')
  const [packageId, setPackageId] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [categoryId, setCategoryId] = useState<string | null>(null)
  const [byTime, setByTime] = useState(true)
  const [time, setTime] = useState<number | ''>('')
  const [typed, setTyped] = useState<TypedPrice | null>(null)
  // Editing labour by time: «Vorschlag übernehmen» sends `unitPrice: null`, the proposal again.
  const [backToProposal, setBackToProposal] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [showMissing, setShowMissing] = useState(false)

  useEffect(() => {
    if (!opened) return
    idempotency.renew()
    setError(null)
    setShowMissing(false)
    setTyped(null)
    setBackToProposal(false)
    setKindTab(position?.kind === 'text' ? 'text' : 'labour')
    setPackageId(null)
    setText(position?.text ?? '')
    setCategoryId(position?.labourCategoryId ?? null)
    setByTime(position ? position.kind === 'labour_time' : true)
    setTime(position?.time ? decimal(position.time.quantity) : '')
  }, [opened, position, idempotency])

  const labour = kindTab === 'labour'
  const fromCatalogue = kindTab === 'package'
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
  // Check 45: only an active package is offered.
  const packages = useQuery({
    queryKey: ['service-catalogue', dealershipId, 'packages', 'active'],
    queryFn: () => api.get<ServicePackageList>(`/dealerships/${dealershipId}/aftersales/service-packages?active=true`),
    enabled: opened && fromCatalogue && dealershipId !== '',
  })
  const packageName = (pkg: ServicePackageRead) => {
    const translated = pkg.nameTranslations[i18n.language.slice(0, 2)]
    return translated && translated.trim() !== '' ? translated : pkg.name
  }
  const packageOptions = (packages.data?.items ?? []).map((pkg) => ({ value: pkg.id, label: packageName(pkg) }))
  const chosenPackage = (packages.data?.items ?? []).find((pkg) => pkg.id === packageId) ?? null
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

  // The unit (check 48): an unchanged time keeps the unit it was entered with; a new or changed
  // time is typed in the unit in force here, the only one the server takes.
  const entryUnit: LabourTimeUnit | null = position?.time?.unit ?? null
  const unitInForce: LabourTimeUnit | null = proposal.data?.timeUnit ?? null
  const timeBefore: number | '' = position?.time ? decimal(position.time.quantity) : ''
  const timeChanged = !editing || time !== timeBefore
  const unit: LabourTimeUnit | null = timeChanged || entryUnit === null ? (unitInForce ?? entryUnit) : entryUnit
  const unitMoved = editing && entryUnit !== null && unitInForce !== null && entryUnit !== unitInForce
  const workUnitsPerHour =
    !timeChanged && position?.time ? position.time.workUnitsPerHour : (proposal.data?.workUnitsPerHour ?? null)
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

  // Mirrors of the server's own shape checks (`service_order_position_incomplete`): a submit
  // that would be refused says what is missing instead; the server still decides. An emptied
  // price field needs a price («Vorschlag übernehmen» brings the proposal back).
  const priceKnown =
    typed !== null
      ? typed.amount !== ''
      : (editing && !backToProposal && position?.unitPrice != null) || (byTime && proposal.data?.found === true)
  const missing = {
    package: fromCatalogue && packageId === null,
    text: !fromCatalogue && text.trim() === '',
    category: labour && categoryId === null,
    time: labour && byTime && time === '',
    price: labour && categoryId !== null && !priceKnown,
  }
  const missingText = (field: keyof typeof missing) =>
    showMissing && missing[field] ? t(`serviceOrder.positionDialog.missing.${field}`) : undefined

  // `confirmed`: sent again after «Trotzdem ändern» while somebody is clocked on the job (check 6).
  const submit = async (confirmed = false) => {
    if (Object.values(missing).some(Boolean)) {
      setShowMissing(true)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      let updated: ServiceOrderRead
      const headers = { 'If-Match': String(order.version) }
      if (fromCatalogue && packageId !== null) {
        const path = `/service-orders/${order.id}/jobs/${jobId}/service-packages`
        const body = confirmed ? { servicePackageId: packageId, confirmWhileClocked: true } : { servicePackageId: packageId }
        updated = await api.post<ServiceOrderRead>(path, body, { ...headers, ...idempotency.headers([path, body]) })
        idempotency.renew()
      } else if (!position) {
        const body: ServiceOrderPositionCreate = { kind, text: text.trim() }
        if (confirmed) body.confirmWhileClocked = true
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
        if (confirmed) body.confirmWhileClocked = true
        if (text.trim() !== position.text) body.text = text.trim()
        if (labour) {
          if (categoryId !== position.labourCategoryId) body.labourCategoryId = categoryId
          if (time !== timeBefore) {
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
      onSubmit={() => void submit()}
      submitLabel={t('serviceOrder.positionDialog.save')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
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
            { value: 'package', label: t('serviceOrder.positionDialog.kinds.package') },
          ]}
          style={{ alignSelf: 'flex-start' }}
        />
      )}
      {fromCatalogue && (
        <Stack gap="xs">
          <Select
            label={t('serviceOrder.positionDialog.package')}
            data={packageOptions}
            value={packageId}
            onChange={setPackageId}
            required
            searchable
            allowDeselect={false}
            error={packages.isError ? t('serviceOrder.positionDialog.packagesError') : missingText('package')}
            data-autofocus
          />
          {packages.isSuccess && packageOptions.length === 0 && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.noPackages')}
            </Text>
          )}
          {chosenPackage && (
            <Stack gap={2} data-testid="package-positions">
              <Text size="xs" c="dimmed">
                {t('serviceOrder.positionDialog.packagePositions')}
              </Text>
              {chosenPackage.positions.map((item) => (
                <Text key={item.id} size="sm">
                  {item.time
                    ? `${item.text} · ${formatLabourTime(t, item.time.quantity, item.time.unit)}`
                    : item.text}
                </Text>
              ))}
            </Stack>
          )}
        </Stack>
      )}
      {!fromCatalogue && (
      <TextInput
        label={t('serviceOrder.positionDialog.text')}
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
        maxLength={1000}
        required
        error={missingText('text')}
        data-autofocus
      />
      )}
      {labour && (
        <Stack gap="sm">
          <Select
            label={t('serviceOrder.positionDialog.labourCategory')}
            data={categoryOptions}
            value={categoryId}
            onChange={(value) => {
              setCategoryId(value)
              setTyped(null)
              // The server's rule (positions.change_position): a changed category takes that
              // category's rate for a proposed price and keeps a typed one. Show what it will do.
              setBackToProposal(editing && byTime && position?.rateSource !== 'typed' && value !== position?.labourCategoryId)
            }}
            required
            allowDeselect={false}
            searchable
            error={categories.isError ? t('serviceOrder.positionDialog.categoriesError') : missingText('category')}
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
              {...hint(
                labourTimeLabel,
                unit === null
                  ? categoryId !== null && proposal.isError
                    ? t('serviceOrder.positionDialog.proposalError')
                    : t('serviceOrder.positionDialog.chooseCategoryFirst')
                  : !byTime
                    ? t('serviceOrder.positionDialog.quantityHint')
                    : undefined,
              )}
              value={time}
              onChange={(value) => setTime(typeof value === 'number' ? value : '')}
              min={0}
              decimalScale={2}
              decimalSeparator="."
              thousandSeparator="'"
              disabled={unit === null}
              required={byTime}
              error={missingText('time')}
            />
            <ChfInput
              {...hint(
                byTime ? t('serviceOrder.positionDialog.rateNet') : t('serviceOrder.positionDialog.fixedNet'),
                byTime
                  ? t('serviceOrder.positionDialog.priceLeads')
                  : `${t('serviceOrder.positionDialog.fixedTyped')} ${t('serviceOrder.positionDialog.priceLeads')}`,
              )}
              value={netShown}
              onChange={typePrice('net')}
              min={0}
              error={missingText('price')}
            />
            <ChfInput
              {...hint(
                byTime ? t('serviceOrder.positionDialog.rateGross') : t('serviceOrder.positionDialog.fixedGross'),
                t('serviceOrder.positionDialog.grossPreview'),
              )}
              value={grossShown}
              onChange={typePrice('gross')}
              min={0}
            />
          </SimpleGrid>
          {unit === 'work_units' && workUnitsPerHour && (
            <Text size="xs" c="dimmed">
              {t('serviceOrder.positionDialog.workUnitsPerHour', { count: decimal(workUnitsPerHour) })}
            </Text>
          )}
          {editing && position?.time && (
            <Text size="xs" c="dimmed">
              {unitMoved && unitInForce
                ? t('serviceOrder.positionDialog.unitMoved', {
                    entry: t(`serviceOrder.unitName.${position.time.unit}`),
                    now: t(`serviceOrder.unitName.${unitInForce}`),
                  })
                : t('serviceOrder.positionDialog.keepsUnit', { unit: t(`serviceOrder.unitName.${position.time.unit}`) })}
            </Text>
          )}
          {proposalLine()}
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
          onConfirmClocked={() => void submit(true)}
        />
      )}
    </FormDialog>
  )
}
