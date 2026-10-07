import { useState } from 'react'
import { Alert, Button, Group, NumberInput, Select, Stack, Table, Text, TextInput } from '@mantine/core'
import { AlertTriangle, Info, ScanSearch } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Picker, type PickerRow } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import { formatCurrencyChf } from '../../utils/format'
import type { ConfiguratorMode } from '../../configurationOptions'
import type {
  BestMatchProposalRead,
  CatalogueVariantRead,
  ConfigurationMatchMethod,
  ConfigurationRead,
  IdentificationRead,
  NewPriceSource,
  ObservedIdentityRead,
  PlateRecordRead,
  VariantCandidateRead,
} from '../../api/types'

/** What the overlay starts phase 2 from: a catalogue variant (or none, for a
 * manual configuration), plus what the input established about the car. */
export interface IdentifiedStart {
  variant: CatalogueVariantRead
  observed: ObservedIdentityRead
  matchMethod: ConfigurationMatchMethod
  /** Set only when the advisor confirmed a FahrzeugeMatch proposal. */
  confirmedBestMatchCode?: number
}

export interface IdentificationPanelProps {
  /** The modes this host allows — a reused configuration in another mode is
   * refused here, the same matrix the host's API enforces. */
  allowedModes: ConfiguratorMode[]
  onStart: (start: IdentifiedStart) => void
  onReuse: (configuration: ConfigurationRead) => void
  /** Carries the typed value into a manual configuration when nothing matched. */
  onQueryChange?: (query: string) => void
}

// The one note an advisor must not see: a dealer without the DAT
// sub-account gets exactly what they had before it existed (KAN-42 exit
// criterion 6) — no message, no dead menu item.
const SILENT_NOTES = new Set(['vin_decode_not_entitled'])

/**
 * FR-C-02 — one input, the waterfall behind it. The advisor types one thing;
 * the backend decides what it is. Every ambiguous answer is the FR-V-06
 * picker (the Vehicles list's own `Picker`-in-an-alert, Wechselschild vs.
 * conflict titled the same way), and **nothing is ever selected for the
 * advisor** — even a single candidate is a row they pick. A best match is a
 * proposal they confirm, never applied.
 */
export function IdentificationPanel({ allowedModes, onStart, onReuse, onQueryChange }: IdentificationPanelProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<IdentificationRead | null>(null)
  // A plate record the advisor picked: its observed facts ride along when
  // the Typenschein it names is resolved next.
  const [carried, setCarried] = useState<Partial<ObservedIdentityRead> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const identify = async (q: string, carry: Partial<ObservedIdentityRead> | null = null) => {
    setLoading(true)
    setError(null)
    try {
      const found = await api.get<IdentificationRead>(`/vehicle-identification?q=${encodeURIComponent(q)}`)
      setResult(found)
      setCarried(carry)
    } catch (err) {
      setResult(null)
      const unrecognised = err instanceof ApiError && err.details?.reason === 'unrecognised_identifier'
      setError(unrecognised ? t('configurator.identify.unrecognised') : t('configurator.identify.error'))
    } finally {
      setLoading(false)
    }
  }

  const observed: ObservedIdentityRead | null = result
    ? { ...result.observed, ...Object.fromEntries(Object.entries(carried ?? {}).filter(([, v]) => v != null)) }
    : null
  // A picked plate record keeps the plate as the method — it is how the car
  // was identified, even though its Typenschein resolved the variant.
  const matchMethod: ConfigurationMatchMethod | null = result
    ? carried?.licencePlate
      ? 'kontrollschild'
      : result.matchMethod
    : null

  const startFromCandidate = async (variantId: string, confirmedBestMatchCode?: number) => {
    if (!observed || !matchMethod) return
    setError(null)
    try {
      const variant = await api.get<CatalogueVariantRead>(`/catalogue/variants/${variantId}`)
      onStart({ variant, observed, matchMethod, confirmedBestMatchCode })
    } catch {
      setError(t('configurator.identify.error'))
    }
  }

  const reuse = async (configurationId: string) => {
    setError(null)
    try {
      const configuration = await api.get<ConfigurationRead>(`/configurations/${configurationId}`)
      if (!allowedModes.includes(configuration.mode)) {
        setError(t('configurator.identify.reuseModeMismatch', { mode: t(`configurator.mode.${configuration.mode}`) }))
        return
      }
      onReuse(configuration)
    } catch {
      setError(t('configurator.identify.error'))
    }
  }

  const pickPlateRecord = (record: PlateRecordRead) =>
    void identify(record.typeApprovalNumber, {
      licencePlate: result?.observed.licencePlate ?? null,
      stammnummer: record.stammnummer,
      firstRegistrationDate: record.firstRegistrationDate,
    })

  const visibleNotes = (result?.notes ?? []).filter((n) => !SILENT_NOTES.has(n))

  return (
    <Stack gap="sm">
      <Group align="flex-end" gap="xs">
        <TextInput
          label={t('configurator.find.idLabel')}
          description={t('configurator.find.idHint')}
          value={query}
          onChange={(e) => {
            setQuery(e.currentTarget.value)
            onQueryChange?.(e.currentTarget.value)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && query.trim()) {
              e.preventDefault()
              void identify(query.trim())
            }
          }}
          w={420}
          maxLength={64}
        />
        <Button
          variant="default"
          leftSection={<ScanSearch size={16} />}
          onClick={() => void identify(query.trim())}
          disabled={!query.trim()}
          loading={loading}
        >
          {t('configurator.identify.submit')}
        </Button>
      </Group>

      {error && <Alert color="red">{error}</Alert>}

      {result && (
        <Stack gap="sm" data-testid="identification-result">
          <Text size="sm" c="dimmed">
            {t('configurator.identify.recognisedAs', { kind: t(`configurator.identify.kind.${result.kind}`) })}
          </Text>

          {visibleNotes.map((note) => (
            <Text key={note} size="sm" c="dimmed">
              {t(`configurator.identify.notes.${note}`)}
            </Text>
          ))}

          {result.existingVehicle && (
            <Alert color="blue" icon={<Info size={16} />} title={t('configurator.identify.existingVehicle.title', { number: result.existingVehicle.vehicleNumber })}>
              <Stack gap="xs">
                {result.existingVehicle.reusableConfigurationId && (
                  <Button
                    size="xs"
                    variant="light"
                    style={{ alignSelf: 'flex-start' }}
                    onClick={() => void reuse(result.existingVehicle!.reusableConfigurationId!)}
                  >
                    {t('configurator.identify.existingVehicle.reuse')}
                  </Button>
                )}
                {result.variants.length === 0 && (
                  <Text size="sm">{t('configurator.identify.existingVehicle.noVariant')}</Text>
                )}
              </Stack>
            </Alert>
          )}

          {result.outcome === 'plate_records' && (
            <Alert
              icon={<AlertTriangle size={16} />}
              color={result.plateRecordsConflict ? 'orange' : 'yellow'}
              title={
                result.plateRecordsConflict
                  ? t('configurator.identify.plateRecords.conflict')
                  : t('configurator.identify.plateRecords.wechselschild')
              }
            >
              <Picker
                rows={result.plateRecords.map(
                  (r, index): PickerRow => ({
                    id: String(index),
                    identifier: r.typeApprovalNumber,
                    label: `${r.brandName} ${r.modelDescription}`,
                    sublabel: [r.stammnummer, r.firstRegistrationDate].filter(Boolean).join(' · '),
                  }),
                )}
                query=""
                onQueryChange={() => {}}
                onSelect={(row) => pickPlateRecord(result.plateRecords[Number(row.id)])}
                autoFocus={false}
                emptyLabel={t('configurator.identify.pickerEmpty')}
              />
            </Alert>
          )}

          {result.variants.length > 0 && (
            <Alert
              icon={<AlertTriangle size={16} />}
              color={result.variants.length > 1 ? 'yellow' : 'gray'}
              title={
                result.variants.length > 1
                  ? t('configurator.identify.variantsTitle')
                  : t('configurator.identify.variantTitle')
              }
            >
              <Picker
                rows={result.variants.map((v) => variantRow(t, v))}
                query=""
                onQueryChange={() => {}}
                onSelect={(row) => void startFromCandidate(row.id)}
                autoFocus={false}
                emptyLabel={t('configurator.identify.pickerEmpty')}
              />
            </Alert>
          )}

          {result.outcome === 'none' && !result.existingVehicle && (
            <Text size="sm">{t('configurator.identify.none')}</Text>
          )}

          {result.bestMatchAvailable && observed?.typeApprovalNumber && (
            <BestMatch
              typeApprovalNumber={observed.typeApprovalNumber}
              firstRegistrationDate={observed.firstRegistrationDate ?? null}
              onConfirm={(proposal) => void startFromCandidate(proposal.candidate.catalogueVariantId, proposal.matchCode)}
            />
          )}
        </Stack>
      )}
    </Stack>
  )
}

function variantRow(t: (k: string, o?: Record<string, unknown>) => string, v: VariantCandidateRead): PickerRow {
  const years =
    v.modelYearTo != null
      ? t('configurator.identify.productionYears', { from: v.modelYearFrom, to: v.modelYearTo })
      : t('configurator.identify.inProduction', { from: v.modelYearFrom })
  const price = v.newPriceForYear ?? v.basePrice
  return {
    id: v.catalogueVariantId,
    identifier: years,
    label: [v.brandDisplayName, v.modelGroupName, v.variantName].filter(Boolean).join(' '),
    sublabel: [v.ps != null ? `${v.ps} PS` : null, price != null ? formatCurrencyChf(Number(price)) : null]
      .filter(Boolean)
      .join(' · '),
  }
}

interface BestMatchProps {
  typeApprovalNumber: string
  firstRegistrationDate: string | null
  onConfirm: (proposal: BestMatchProposalRead) => void
}

/** FR-C-02 step 5 — FahrzeugeMatch needs a Neupreis, and the advisor says
 * where it came from (a wrong year's price returns a plausible wrong car).
 * The answer is shown field by field and applied only on confirmation. */
function BestMatch({ typeApprovalNumber, firstRegistrationDate, onConfirm }: BestMatchProps) {
  const { t } = useTranslation()
  const [newPrice, setNewPrice] = useState<number | ''>('')
  const [source, setSource] = useState<NewPriceSource | null>(null)
  const [modelDescription, setModelDescription] = useState('')
  const [proposal, setProposal] = useState<BestMatchProposalRead | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ask = async () => {
    if (newPrice === '' || !source) return
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ typeApprovalNumber, newPrice: String(newPrice), newPriceSource: source })
      if (modelDescription.trim()) params.set('modelDescription', modelDescription.trim())
      if (firstRegistrationDate) params.set('firstRegistrationDate', firstRegistrationDate)
      setProposal(await api.get<BestMatchProposalRead>(`/vehicle-identification/best-match?${params}`))
    } catch {
      setProposal(null)
      setError(t('configurator.bestMatch.error'))
    } finally {
      setLoading(false)
    }
  }

  const label = proposal
    ? [proposal.candidate.brandDisplayName, proposal.candidate.modelGroupName, proposal.candidate.variantName]
        .filter(Boolean)
        .join(' ')
    : ''

  return (
    <Stack gap="xs" data-testid="best-match">
      <Text fw={600} size="sm">
        {t('configurator.bestMatch.title')}
      </Text>
      <Text size="sm" c="dimmed">
        {t('configurator.bestMatch.hint')}
      </Text>
      <Group align="flex-end" gap="xs">
        <NumberInput
          label={t('configurator.bestMatch.newPrice')}
          value={newPrice}
          onChange={(v) => setNewPrice(v === '' ? '' : Number(v))}
          min={1}
          w={160}
        />
        <Select
          label={t('configurator.bestMatch.source.label')}
          data={(['catalogue', 'document', 'customer'] as NewPriceSource[]).map((value) => ({
            value,
            label: t(`configurator.bestMatch.source.${value}`),
          }))}
          value={source}
          onChange={(v) => setSource(v as NewPriceSource | null)}
          w={260}
        />
        <TextInput
          label={t('configurator.bestMatch.modelDescription')}
          value={modelDescription}
          onChange={(e) => setModelDescription(e.currentTarget.value)}
          w={220}
        />
        <Button variant="default" onClick={() => void ask()} disabled={newPrice === '' || !source} loading={loading}>
          {t('configurator.bestMatch.ask')}
        </Button>
      </Group>
      {error && <Alert color="red">{error}</Alert>}
      {proposal && (
        <Alert
          color={proposal.matchCode === 1 ? 'blue' : 'orange'}
          title={t('configurator.bestMatch.proposal', { label })}
          data-testid="best-match-proposal"
        >
          <Stack gap="xs">
            <Text size="sm">{t(`configurator.bestMatch.matchCode.${proposal.matchCode}`)}</Text>
            <Table withRowBorders={false}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th />
                  <Table.Th>{t('configurator.bestMatch.entered')}</Table.Th>
                  <Table.Th>{t('configurator.bestMatch.matched')}</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {proposal.fields.map((f) => (
                  <Table.Tr key={f.field}>
                    <Table.Td>{t(`configurator.bestMatch.fields.${f.field}`)}</Table.Td>
                    <Table.Td>{f.entered ?? '—'}</Table.Td>
                    <Table.Td>{f.matched ?? '—'}</Table.Td>
                    <Table.Td>
                      <Text size="sm" c={f.agrees ? 'teal' : 'orange'}>
                        {f.agrees ? t('configurator.bestMatch.agrees') : t('configurator.bestMatch.differs')}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            <Group gap="xs">
              <Button size="xs" onClick={() => onConfirm(proposal)}>
                {t('configurator.bestMatch.confirm')}
              </Button>
              <Button size="xs" variant="subtle" onClick={() => setProposal(null)}>
                {t('configurator.bestMatch.discard')}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}
    </Stack>
  )
}
