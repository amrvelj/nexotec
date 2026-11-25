import { useState } from 'react'
import {
  Alert,
  Button,
  Group,
  Paper,
  SegmentedControl,
  Select,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { Badge, type BadgeTone, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type {
  AreaSettingsRead,
  LabourCategoryCandidateList,
  LabourRateAgreementPage,
  LabourRateCategoryList,
  LabourRateCategoryRead,
  LabourRateRead,
  LocationList,
  SettingSlotRead,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import {
  AgreementDialog,
  CategoryDialog,
  RateDialog,
  WithdrawDialog,
  type WithdrawTarget,
} from '../components/labourRates/LabourRateDialogs'
import { rateAmountText } from '../components/labourRates/rateAmount'
import { apiRefusalText } from '../utils/apiRefusal'
import { formatDateOnly } from '../utils/format'

const RATES_ENTERED = 'labour_rates.rates_entered'
const TIME_UNIT = 'labour_rates.time_unit'
const WORK_UNITS_PER_HOUR = 'labour_rates.work_units_per_hour'

const labourRatesKey = (dealershipId: string) => ['labour-rates', dealershipId]

const STATUS_TONE: Record<string, BadgeTone> = {
  in_force: 'success',
  scheduled: 'informational',
  superseded: 'slate',
  withdrawn: 'slate',
}

/**
 * Dealer Administration › Labour rates (KAN-806; D-A-19; Aftersales slice 1,
 * section 5; rulings 177, 178, 183). The Betrieb's labour categories, a rate
 * per category and location from a date, who works in which category, and
 * the fixed rates agreed with contacts — plus the three settings that belong
 * to them (Aftersales PRD 8.3): O16 net or with VAT, O18 hours or work units,
 * O19 work units per hour. It is also the settings area `labour_rates`
 * (`/administration/settings/labour_rates`), where the setup checklist links
 * O16.
 *
 * A rate is written once (ruling 178): no edit; a new price is a new rate
 * from today or later; one still to come may be withdrawn. Aftersales,
 * parts, finance and the manager read; the manager writes.
 */
export function LabourRatesPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('labourRates.title')])
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const canWrite = useCanWrite('labour_rates')
  const queryClient = useQueryClient()
  const base = `/dealerships/${dealershipId}`

  const categories = useQuery({
    queryKey: [...labourRatesKey(dealershipId), 'categories'],
    queryFn: () => api.get<LabourRateCategoryList>(`${base}/labour-rate-categories`),
    enabled: dealershipId !== '',
  })
  const settings = useQuery({
    queryKey: [...labourRatesKey(dealershipId), 'settings'],
    queryFn: () => api.get<AreaSettingsRead>(`${base}/setting-areas/labour_rates`),
    enabled: dealershipId !== '',
  })
  const locations = useQuery({
    queryKey: ['locations', dealershipId],
    queryFn: () => api.get<LocationList>(`${base}/locations`),
    enabled: dealershipId !== '',
  })
  // Cursor-paged (insurers and fleets make it grow); withdrawn rows shown, as withdrawn rates are.
  const agreements = useInfiniteQuery({
    queryKey: [...labourRatesKey(dealershipId), 'agreements'],
    queryFn: ({ pageParam }) =>
      api.get<LabourRateAgreementPage>(
        `${base}/labour-rate-agreements?includeWithdrawn=true&limit=50${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`,
      ),
    initialPageParam: '',
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: dealershipId !== '',
  })
  const agreementRows = agreements.data?.pages.flatMap((page) => page.items) ?? []
  const candidates = useQuery({
    queryKey: [...labourRatesKey(dealershipId), 'candidates'],
    queryFn: () => api.get<LabourCategoryCandidateList>(`${base}/labour-category-candidates`),
    enabled: dealershipId !== '' && canWrite,
  })

  const [error, setError] = useState<string | null>(null)
  const [categoryDialog, setCategoryDialog] = useState<{ category: LabourRateCategoryRead | null } | null>(null)
  const [rateFor, setRateFor] = useState<LabourRateCategoryRead | null>(null)
  const [agreementOpen, setAgreementOpen] = useState(false)
  const [withdrawing, setWithdrawing] = useState<WithdrawTarget | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: labourRatesKey(dealershipId) })
  const slot = (key: string): SettingSlotRead | undefined =>
    settings.data?.settings.find((setting) => setting.declaration.key === key)?.dealership
  const entered = slot(RATES_ENTERED)?.value?.choice ?? null
  // O16 counts at the rate's location (ruling 175): a location's own value, else the Betrieb's.
  const enteredAt = (locationId: string | null): string | null => {
    const setting = settings.data?.settings.find((s) => s.declaration.key === RATES_ENTERED)
    const own = setting?.locations.find((l) => l.locationId === locationId && l.isSet)
    return own?.value?.choice ?? entered
  }
  const anyEntered =
    entered !== null ||
    (settings.data?.settings.find((s) => s.declaration.key === RATES_ENTERED)?.locations ?? []).some((l) => l.isSet)
  // «Not set» is a state of its own, never an empty field, a zero or «off» (KAN-805, design):
  // O18 not set selects nothing and says it acts as hours; O19 not set says so.
  const timeUnit = slot(TIME_UNIT)?.value?.choice ?? null
  const perHour = slot(WORK_UNITS_PER_HOUR)?.value?.number ?? null
  const locationName = (id: string) => locations.data?.items.find((location) => location.id === id)?.name ?? '—'
  const categoryName = (id: string) => categories.data?.items.find((category) => category.id === id)?.name ?? '—'

  const writeSetting = async (key: string, value: Record<string, string> | null) => {
    setError(null)
    try {
      await api.put(`${base}/setting-values/${key}`, { value }, { 'If-Match': String(slot(key)?.version ?? 0) })
      await refresh()
    } catch (err) {
      setError(apiRefusalText(t, err, 'labourRates.saveError'))
    }
  }

  const run = async (call: () => Promise<unknown>) => {
    setError(null)
    try {
      await call()
      await refresh()
    } catch (err) {
      setError(apiRefusalText(t, err, 'labourRates.saveError'))
    }
  }

  const loading = categories.isLoading || settings.isLoading
  const activeCategories = (categories.data?.items ?? []).filter((category) => category.isActive)

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('labourRates.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('labourRates.subtitle')}
        </Text>
      </div>
      {(categories.isError || settings.isError) && (
        <Alert color="red" role="alert">
          {apiRefusalText(t, categories.error ?? settings.error, 'labourRates.loadError')}
        </Alert>
      )}
      {error && (
        <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {loading && <Skeleton height={240} radius="md" />}
      {!loading && !canWrite && (
        <Text c="dimmed" fz="sm">
          {t('labourRates.readOnly')}
        </Text>
      )}

      {settings.data && (
        <Paper withBorder radius="md" p="md" aria-label={t('labourRates.settings.title')} component="section">
          <Title order={4} mb="xs">
            {t('labourRates.settings.title')}
          </Title>
          <Stack gap="sm">
            <div>
              <Group gap="xs">
                <Text fz="sm" fw={500}>
                  {t('settings.keys.labour_rates.rates_entered.label')}
                </Text>
                {entered === null && <Badge tone="warning">{t('labourRates.settings.notSet')}</Badge>}
              </Group>
              <SegmentedControl
                aria-label={t('settings.keys.labour_rates.rates_entered.label')}
                disabled={!canWrite}
                value={entered ?? ''}
                onChange={(value) => void writeSetting(RATES_ENTERED, { choice: value })}
                data={[
                  { value: 'net', label: t('settings.keys.labour_rates.rates_entered.options.net') },
                  { value: 'gross', label: t('settings.keys.labour_rates.rates_entered.options.gross') },
                ]}
              />
              {entered === null && (
                <Alert color="yellow" variant="light" mt="xs">
                  {anyEntered ? t('labourRates.settings.enteredAtLocationsOnly') : t('labourRates.settings.enteredNotSet')}
                </Alert>
              )}
            </div>
            <Group align="flex-end" gap="lg">
              <div>
                <Group gap="xs">
                  <Text fz="sm" fw={500}>
                    {t('settings.keys.labour_rates.time_unit.label')}
                  </Text>
                  {timeUnit === null && <Badge tone="slate">{t('labourRates.settings.notSetActsAsHours')}</Badge>}
                </Group>
                <SegmentedControl
                  aria-label={t('settings.keys.labour_rates.time_unit.label')}
                  disabled={!canWrite}
                  value={timeUnit ?? ''}
                  onChange={(value) => void writeSetting(TIME_UNIT, { choice: value })}
                  data={[
                    { value: 'hours', label: t('settings.keys.labour_rates.time_unit.options.hours') },
                    {
                      value: 'work_units',
                      label: t('settings.keys.labour_rates.time_unit.options.work_units'),
                      // Check 49: work units only with work units per hour.
                      disabled: perHour === null,
                    },
                  ]}
                />
              </div>
              <WorkUnitsPerHourField
                value={perHour}
                disabled={!canWrite}
                onSave={(value) => void writeSetting(WORK_UNITS_PER_HOUR, value === null ? null : { number: value })}
              />
            </Group>
            {settings.data.settings.some((setting) => !setting.dealership.fitsDeclaration) && (
              <Alert color="yellow" variant="light">
                {t('labourRates.settings.staleValue')}
              </Alert>
            )}
            <Text c="dimmed" fz="xs">
              {t('labourRates.settings.perHourNote')}
            </Text>
          </Stack>
        </Paper>
      )}

      {categories.data && (
        <Stack gap="sm" component="section" aria-label={t('labourRates.categories.title')}>
          <Group justify="space-between">
            <Title order={4}>{t('labourRates.categories.title')}</Title>
            {canWrite && (
              <Button size="xs" onClick={() => setCategoryDialog({ category: null })}>
                {t('labourRates.categories.add')}
              </Button>
            )}
          </Group>
          {categories.data.items.length === 0 && (
            <Text c="dimmed" fz="sm">
              {t('labourRates.categories.empty')}
            </Text>
          )}
          {categories.data.items.map((category) => (
            <CategoryCard
              key={category.id}
              category={category}
              canWrite={canWrite}
              canEnterRates={anyEntered}
              locationName={locationName}
              candidates={candidates.data?.items ?? []}
              onEdit={() => setCategoryDialog({ category })}
              onToggleActive={() =>
                void run(() =>
                  api.patch(
                    `${base}/labour-rate-categories/${category.id}`,
                    { isActive: !category.isActive },
                    { 'If-Match': String(category.version) },
                  ),
                )
              }
              onAddRate={() => setRateFor(category)}
              onWithdrawRate={(rate) => setWithdrawing({ kind: 'rate', id: rate.id, version: rate.version })}
              onAddMember={(userId) =>
                void run(() => api.post(`${base}/labour-rate-categories/${category.id}/members`, { userId }))
              }
              onRemoveMember={(userId) =>
                void run(() => api.delete(`${base}/labour-rate-categories/${category.id}/members/${userId}`))
              }
            />
          ))}
        </Stack>
      )}

      {agreements.data && (
        <Stack gap="sm" component="section" aria-label={t('labourRates.agreements.title')}>
          <Group justify="space-between">
            <div>
              <Title order={4}>{t('labourRates.agreements.title')}</Title>
              <Text c="dimmed" fz="sm">
                {t('labourRates.agreements.subtitle')}
              </Text>
            </div>
            {canWrite && (
              <Button size="xs" disabled={activeCategories.length === 0} onClick={() => setAgreementOpen(true)}>
                {t('labourRates.agreements.add')}
              </Button>
            )}
          </Group>
          {agreementRows.length === 0 ? (
            <Text c="dimmed" fz="sm">
              {t('labourRates.agreements.empty')}
            </Text>
          ) : (
            <Paper withBorder radius="md">
              <Table striped aria-label={t('labourRates.agreements.title')}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t('labourRates.agreements.columns.contact')}</Table.Th>
                    <Table.Th>{t('labourRates.agreements.columns.category')}</Table.Th>
                    <Table.Th>{t('labourRates.columns.validFrom')}</Table.Th>
                    <Table.Th>{t('labourRates.columns.amount')}</Table.Th>
                    <Table.Th>{t('labourRates.columns.status')}</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {agreementRows.map((agreement) => (
                    <Table.Tr key={agreement.id}>
                      <Table.Td>{agreement.contactLabel}</Table.Td>
                      <Table.Td>{categoryName(agreement.categoryId)}</Table.Td>
                      <Table.Td>{formatDateOnly(agreement.validFrom)}</Table.Td>
                      <Table.Td>
                        {agreement.kind === 'end' || agreement.amount === null || agreement.entered === null
                          ? t('labourRates.agreements.ends')
                          : `${rateAmountText(agreement.amount)} ${t(`labourRates.entered.${agreement.entered}`)}`}
                      </Table.Td>
                      <Table.Td>
                        <Badge tone={STATUS_TONE[agreement.status] ?? 'slate'} dot>
                          {t(`labourRates.status.${agreement.status}`)}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        {canWrite && agreement.status === 'scheduled' && (
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            color="red"
                            onClick={() =>
                              setWithdrawing({ kind: 'agreement', id: agreement.id, version: agreement.version })
                            }
                          >
                            {t('labourRates.withdraw.action')}
                          </Button>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>
          )}
          {agreements.hasNextPage && (
            <Button
              size="xs"
              variant="default"
              loading={agreements.isFetchingNextPage}
              onClick={() => void agreements.fetchNextPage()}
            >
              {t('labourRates.agreements.more')}
            </Button>
          )}
        </Stack>
      )}

      {categoryDialog && (
        <CategoryDialog
          category={categoryDialog.category}
          onClose={() => setCategoryDialog(null)}
          onSave={async (body) => {
            const category = categoryDialog.category
            if (category) {
              await api.patch(`${base}/labour-rate-categories/${category.id}`, body, {
                'If-Match': String(category.version),
              })
            } else {
              await api.post(`${base}/labour-rate-categories`, body)
            }
            setCategoryDialog(null)
            await refresh()
          }}
        />
      )}
      {rateFor && (
        <RateDialog
          category={rateFor}
          enteredAt={enteredAt}
          today={categories.data?.today ?? ''}
          locations={(locations.data?.items ?? []).filter((location) => location.isActive)}
          onClose={() => setRateFor(null)}
          onSave={async (body) => {
            await api.post(`${base}/labour-rate-categories/${rateFor.id}/rates`, body)
            setRateFor(null)
            await refresh()
          }}
        />
      )}
      {agreementOpen && (
        <AgreementDialog
          categories={activeCategories}
          entered={entered}
          today={categories.data?.today ?? ''}
          onClose={() => setAgreementOpen(false)}
          onSave={async (body) => {
            await api.post(`${base}/labour-rate-agreements`, body)
            setAgreementOpen(false)
            await refresh()
          }}
        />
      )}
      {withdrawing && (
        <WithdrawDialog
          target={withdrawing}
          onClose={() => setWithdrawing(null)}
          onConfirm={async () => {
            const path =
              withdrawing.kind === 'rate'
                ? `${base}/labour-rates/${withdrawing.id}/withdraw`
                : `${base}/labour-rate-agreements/${withdrawing.id}/withdraw`
            await api.post(path, undefined, { 'If-Match': String(withdrawing.version) })
            setWithdrawing(null)
            await refresh()
          }}
        />
      )}
    </Stack>
  )
}

function WorkUnitsPerHourField({
  value,
  disabled,
  onSave,
}: {
  value: string | null
  disabled: boolean
  onSave: (value: string | null) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<string>(value ?? '')
  const [shown, setShown] = useState<string | null>(value)
  if (shown !== value) {
    setShown(value)
    setDraft(value ?? '')
  }
  const commit = () => {
    const trimmed = draft.trim()
    if (trimmed === (value ?? '')) return
    onSave(trimmed === '' ? null : trimmed)
  }
  return (
    <TextInput
      label={
        value === null ? (
          <Group gap="xs" component="span">
            <span>{t('settings.keys.labour_rates.work_units_per_hour.label')}</span>
            <Badge tone="slate">{t('labourRates.settings.notSet')}</Badge>
          </Group>
        ) : (
          t('settings.keys.labour_rates.work_units_per_hour.label')
        )
      }
      aria-label={t('settings.keys.labour_rates.work_units_per_hour.label')}
      placeholder={t('labourRates.settings.notSet')}
      inputMode="numeric"
      w={200}
      disabled={disabled}
      value={draft}
      onChange={(event) => setDraft(event.currentTarget.value.replace(/[^0-9]/g, ''))}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit()
      }}
    />
  )
}

function CategoryCard({
  category,
  canWrite,
  canEnterRates,
  locationName,
  candidates,
  onEdit,
  onToggleActive,
  onAddRate,
  onWithdrawRate,
  onAddMember,
  onRemoveMember,
}: {
  category: LabourRateCategoryRead
  canWrite: boolean
  /** O16 is set for the Betrieb or at a location: a rate can be entered somewhere. */
  canEnterRates: boolean
  locationName: (id: string) => string
  candidates: LabourCategoryCandidateList['items']
  onEdit: () => void
  onToggleActive: () => void
  onAddRate: () => void
  onWithdrawRate: (rate: LabourRateRead) => void
  onAddMember: (userId: string) => void
  onRemoveMember: (userId: string) => void
}) {
  const { t } = useTranslation()
  const memberIds = new Set(category.members.map((member) => member.userId))
  const free = candidates.filter((candidate) => !memberIds.has(candidate.userId))
  return (
    <Paper withBorder radius="md" p="md" aria-label={category.name} component="article">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <Text fw={600}>{category.name}</Text>
          {!category.isActive && <Badge tone="slate">{t('labourRates.categories.inactive')}</Badge>}
        </Group>
        {canWrite && (
          <Group gap="xs">
            <Button size="compact-xs" variant="default" onClick={onEdit}>
              {t('labourRates.categories.edit')}
            </Button>
            <Button size="compact-xs" variant="subtle" onClick={onToggleActive}>
              {category.isActive ? t('labourRates.categories.deactivate') : t('labourRates.categories.reactivate')}
            </Button>
          </Group>
        )}
      </Group>
      {category.rates.length === 0 ? (
        <Text c="dimmed" fz="sm">
          {t('labourRates.rates.empty')}
        </Text>
      ) : (
        <Table aria-label={t('labourRates.rates.title', { name: category.name })}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('labourRates.columns.location')}</Table.Th>
              <Table.Th>{t('labourRates.columns.validFrom')}</Table.Th>
              <Table.Th>{t('labourRates.columns.amount')}</Table.Th>
              <Table.Th>{t('labourRates.columns.status')}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {category.rates.map((rate) => (
              <Table.Tr key={rate.id}>
                <Table.Td>{locationName(rate.locationId)}</Table.Td>
                <Table.Td>{formatDateOnly(rate.validFrom)}</Table.Td>
                <Table.Td>
                  {rateAmountText(rate.amount)} {t(`labourRates.entered.${rate.entered}`)}
                </Table.Td>
                <Table.Td>
                  <Badge tone={STATUS_TONE[rate.status] ?? 'slate'} dot>
                    {t(`labourRates.status.${rate.status}`)}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  {canWrite && rate.status === 'scheduled' && (
                    <Button size="compact-xs" variant="subtle" color="red" onClick={() => onWithdrawRate(rate)}>
                      {t('labourRates.withdraw.action')}
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {canWrite && category.isActive && (
        <Button size="xs" variant="light" mt="xs" disabled={!canEnterRates} onClick={onAddRate}>
          {t('labourRates.rates.add')}
        </Button>
      )}
      <Group gap="xs" mt="sm" align="center">
        <Text fz="sm" fw={500}>
          {t('labourRates.members.title')}
        </Text>
        {category.members.length === 0 && (
          <Text c="dimmed" fz="sm">
            {t('labourRates.members.empty')}
          </Text>
        )}
        {category.members.map((member) => (
          <Group key={member.userId} gap={4}>
            <Badge tone={member.isActive ? 'informational' : 'slate'} strikethrough={!member.isActive}>
              {member.displayName}
            </Badge>
            {canWrite && (
              <Button
                size="compact-xs"
                variant="subtle"
                aria-label={t('labourRates.members.remove', { name: member.displayName })}
                onClick={() => onRemoveMember(member.userId)}
              >
                <X size={12} />
              </Button>
            )}
          </Group>
        ))}
        {canWrite && free.length > 0 && (
          <Select
            size="xs"
            w={220}
            placeholder={t('labourRates.members.add')}
            aria-label={t('labourRates.members.add')}
            data={free.map((candidate) => ({ value: candidate.userId, label: candidate.displayName }))}
            value={null}
            onChange={(userId) => {
              if (userId) onAddMember(userId)
            }}
          />
        )}
      </Group>
    </Paper>
  )
}
