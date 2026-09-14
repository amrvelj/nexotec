import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Info, PencilRuler } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailTabs, StickyActionFooter } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { CatalogueBrowseGrid } from '../catalogue/CatalogueBrowseGrid'
import { ConfigurationSummaryCard } from './ConfigurationSummaryCard'
import { OptionsTab } from './OptionsTab'
import { ColourWheelsTab } from './ColourWheelsTab'
import { ImagesTab } from './ImagesTab'
import {
  SPEC_FIELD_GROUPS,
  SPEC_REF_LIST_CODES,
  configuratorModeOptions,
  specFieldLabelKey,
  type ConfiguratorMode,
} from '../../configurationOptions'
import type {
  CatalogueOptionRead,
  CatalogueSpecificationRead,
  CatalogueVariantRead,
  ConfigurationOptionInput,
  ConfigurationOptionRead,
  ConfigurationRead,
  ReferenceValuePage,
  VehicleSpecBlockRead,
} from '../../api/types'

// KAN-43 (C-E) — option_group/equipment_feature join the spec block's own
// reference lists in the one label-lookup query this file already has;
// nothing here needs its own fetch.
const OPTION_REF_LIST_CODES = [...SPEC_REF_LIST_CODES, 'option_group', 'equipment_feature']

type Phase = 'find' | 'configure'
type Section = 'specification' | 'options' | 'colour' | 'images' | 'summary'

interface Draft {
  source: 'provider' | 'manual'
  catalogueVariantId: string | null
  brandDisplayName: string | null
  modelGroupName: string | null
  variantName: string | null
  spec: VehicleSpecBlockRead
  /** spec keys the advisor changed after a catalogue copy */
  overridden: Set<string>
  vin: string
  firstRegistrationDate: string
  mileageKm: string
  exteriorColour: string
  interiorColour: string
  /** Empty string means "not set" (matches `mileageKm`'s own convention) —
   * `''` never reaches the wire as `NaN`, `save()` maps it to `null`. */
  exteriorColourSurcharge: string
  interiorColourSurcharge: string
  wheels: string
  wheelsSurcharge: string
  notes: string
  /** Explicit overrides only (KAN-43/C-E) — see `OptionsTabProps.selected`'s
   * own doc comment for what "explicit" means here. */
  selectedOptions: Map<string, ConfigurationOptionInput>
}

const EMPTY_SPEC: VehicleSpecBlockRead = {}

function draftFromVariant(v: CatalogueVariantRead): Draft {
  return {
    source: 'provider',
    catalogueVariantId: v.id,
    brandDisplayName: v.brandDisplayName,
    modelGroupName: v.modelGroupName,
    variantName: v.variantName,
    spec: { ...v.spec },
    overridden: new Set(),
    vin: '',
    firstRegistrationDate: '',
    mileageKm: '',
    exteriorColour: '',
    interiorColour: '',
    exteriorColourSurcharge: '',
    interiorColourSurcharge: '',
    wheels: '',
    wheelsSurcharge: '',
    notes: '',
    selectedOptions: new Map(),
  }
}

function emptyManualDraft(): Draft {
  return {
    source: 'manual',
    catalogueVariantId: null,
    brandDisplayName: '',
    modelGroupName: '',
    variantName: '',
    spec: { ...EMPTY_SPEC },
    overridden: new Set(),
    vin: '',
    firstRegistrationDate: '',
    mileageKm: '',
    exteriorColour: '',
    interiorColour: '',
    exteriorColourSurcharge: '',
    interiorColourSurcharge: '',
    wheels: '',
    wheelsSurcharge: '',
    notes: '',
    selectedOptions: new Map(),
  }
}

export interface ConfiguratorOverlayProps {
  /** Prefilled from the host, changeable in phase 1. */
  initialMode?: ConfiguratorMode
  /** Called with the saved configuration when the advisor commits. */
  onCommitted: (configuration: ConfigurationRead) => void
  /** Close without committing (the overlay's close button / Escape also
   * calls this via the host's `useOverlay().pop`). */
  onClose: () => void
}

/**
 * The two-phase configurator (ADR-059 overlay, ADR-068 entity). Phase 1
 * gates phase 2 (options can't be offered before a car is known); nothing
 * inside phase 2 is sequenced — **not a wizard**. One component, both
 * modes. No standalone route: a host renders this inside an
 * `OverlayProvider` layer (C-F wires the real hosts).
 */
export function ConfiguratorOverlay({ initialMode = 'build', onCommitted, onClose }: ConfiguratorOverlayProps) {
  const { t } = useTranslation()
  const [phase, setPhase] = useState<Phase>('find')
  const [mode, setMode] = useState<ConfiguratorMode>(initialMode)
  const [section, setSection] = useState<Section>('specification')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [vinInput, setVinInput] = useState('')
  const [configurationId, setConfigurationId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refLabels = useReferenceLabels()

  const startFromVariant = (variant: CatalogueVariantRead) => {
    const d = draftFromVariant(variant)
    if (vinInput.trim()) d.vin = vinInput.trim()
    setDraft(d)
    setPhase('configure')
  }

  const startManual = () => {
    const d = emptyManualDraft()
    if (vinInput.trim()) d.vin = vinInput.trim()
    setDraft(d)
    setPhase('configure')
  }

  const setSpecField = (key: keyof VehicleSpecBlockRead, value: number | string | null) => {
    setDraft((prev) => {
      if (!prev) return prev
      const overridden = new Set(prev.overridden)
      if (prev.source === 'provider') overridden.add(key as string)
      return { ...prev, spec: { ...prev.spec, [key]: value === '' ? null : value }, overridden }
    })
  }

  // KAN-43 (C-E) — the catalogue's own options/colours/tyres/images for
  // this variant. Never fetched for a manual configuration (no variant to
  // ask about) — see OptionsTab's own "no catalogue data" state.
  const specQuery = useQuery({
    queryKey: ['catalogue-variant-specification', draft?.catalogueVariantId],
    queryFn: () => api.get<CatalogueSpecificationRead>(`/catalogue/variants/${draft!.catalogueVariantId}/specification`),
    enabled: draft?.catalogueVariantId != null,
  })

  const handleOptionToggle = (option: CatalogueOptionRead) => {
    setDraft((prev) => {
      if (!prev) return prev
      const selectedOptions = new Map(prev.selectedOptions)
      const existing = selectedOptions.get(option.id)
      if (existing) {
        // Flip `selected` rather than deleting the entry — an equipment-
        // feature correction the advisor made while this option was
        // checked must survive an uncheck/recheck in the same session,
        // not silently reset to the catalogue's own defaults (Q-C-5:
        // "the user should always be able to correct it").
        selectedOptions.set(option.id, { ...existing, selected: !existing.selected })
      } else {
        selectedOptions.set(option.id, toConfigurationOptionInput(option))
      }
      return { ...prev, selectedOptions }
    })
  }

  const handleAddPackageContents = (options: CatalogueOptionRead[]) => {
    setDraft((prev) => {
      if (!prev) return prev
      const selectedOptions = new Map(prev.selectedOptions)
      for (const o of options) {
        const existing = selectedOptions.get(o.id)
        selectedOptions.set(o.id, existing ? { ...existing, selected: true } : toConfigurationOptionInput(o))
      }
      return { ...prev, selectedOptions }
    })
  }

  const handleUpdateFeatures = (variantOptionId: string, features: string[]) => {
    setDraft((prev) => {
      if (!prev) return prev
      const selectedOptions = new Map(prev.selectedOptions)
      const existing = selectedOptions.get(variantOptionId)
      if (existing) {
        selectedOptions.set(variantOptionId, { ...existing, equipmentFeatures: features })
      } else {
        // An included option the advisor is correcting for the first time
        // — snapshot it from the catalogue row so the correction has
        // somewhere to live (see OptionsTab's own doc comment on `selected`).
        const catalogueOption = specQuery.data?.options.find((o) => o.id === variantOptionId)
        if (!catalogueOption) return prev
        selectedOptions.set(variantOptionId, {
          ...toConfigurationOptionInput(catalogueOption),
          equipmentFeatures: features,
        })
      }
      return { ...prev, selectedOptions }
    })
  }

  const optionGroupLabel = (code: string) =>
    refLabels.data?.option_group?.find((r) => r.valueCode === code)?.label ?? null
  const equipmentFeatureLabel = (code: string) =>
    refLabels.data?.equipment_feature?.find((r) => r.valueCode === code)?.label ?? code
  const equipmentFeatureOptions = (refLabels.data?.equipment_feature ?? []).map((r) => ({
    value: r.valueCode,
    label: r.label,
  }))

  const save = async () => {
    if (!draft) return
    setSaving(true)
    setError(null)
    try {
      const specPayload = draft.source === 'provider'
        ? pickSpec(draft.spec, draft.overridden)
        : nonEmptySpec(draft.spec)

      const body: Record<string, unknown> = {
        mode,
        vin: draft.vin || null,
        firstRegistrationDate: draft.firstRegistrationDate || null,
        mileageKm: draft.mileageKm ? Number(draft.mileageKm) : null,
        exteriorColour: draft.exteriorColour || null,
        interiorColour: draft.interiorColour || null,
        // KAN-43 (C-E) / FR-C-07, FR-C-08 — a surcharge only means anything
        // in build mode (a new-vehicle price build-up); record mode never
        // shows the inputs (see the 'colour' section below), so nothing
        // here forces record-mode drafts to carry a stray non-null value.
        exteriorColourSurcharge: draft.exteriorColourSurcharge || null,
        interiorColourSurcharge: draft.interiorColourSurcharge || null,
        wheels: draft.wheels || null,
        wheelsSurcharge: draft.wheelsSurcharge || null,
        notes: draft.notes || null,
      }

      let config: ConfigurationRead
      const isFirstSave = configurationId === null
      if (isFirstSave) {
        config = await api.post<ConfigurationRead>(
          '/configurations',
          {
            ...body,
            source: draft.source,
            matchMethod: draft.source === 'provider' ? 'catalogue_browse' : 'manual',
            catalogueVariantId: draft.catalogueVariantId,
            brandDisplayName: draft.brandDisplayName || null,
            modelGroupName: draft.modelGroupName || null,
            variantName: draft.variantName || null,
            spec: specPayload,
          },
          { 'Idempotency-Key': crypto.randomUUID() },
        )
        setConfigurationId(config.id)
      } else {
        config = await api.patch<ConfigurationRead>(
          `/configurations/${configurationId}`,
          { ...body, spec: specPayload },
          { 'If-Match': String(currentVersion) },
        )
      }
      // Recorded immediately — the options PATCH below is a second, separate
      // call, and if it fails after this one already succeeded, the local
      // version must still match what the server actually holds. Leaving
      // this until after both calls would strand `currentVersion` behind
      // the server on a partial failure, and the very next save would send
      // a stale If-Match and 409 forever.
      setCurrentVersion(config.version)

      // Options ride a separate PATCH (C-C's own endpoint, unchanged by
      // this ticket) — skipped on a brand-new configuration that never had
      // an option touched (nothing to say yet), and skipped whenever the
      // catalogue specification itself hasn't actually loaded (a manual
      // configuration correctly has no spec at all; a provider one with a
      // spec that's merely still loading, or failed to load, must NOT
      // resend an authoritative empty list — that would silently wipe out
      // every included/standard option the fetch just hasn't told us about
      // yet). Every later save with a loaded spec resends the full current
      // set, even empty, so removing every option and saving again
      // actually clears them server-side.
      const canSyncOptions = draft.catalogueVariantId == null || specQuery.data != null
      const optionsPayload = finalOptionsPayload(draft, specQuery.data)
      if (canSyncOptions && (!isFirstSave || optionsPayload.length > 0)) {
        try {
          config = await api.patch<ConfigurationRead>(
            `/configurations/${config.id}/options`,
            { options: optionsPayload },
            { 'If-Match': String(config.version) },
          )
          setCurrentVersion(config.version)
        } catch {
          setError(t('configurator.saveOptionsError'))
          return
        }
      }

      onCommitted(config)
    } catch {
      setError(t('configurator.saveError'))
    } finally {
      setSaving(false)
    }
  }

  const [currentVersion, setCurrentVersion] = useState(0)

  const readModel: ConfigurationRead | null = useMemo(() => {
    if (!draft) return null
    return previewRead(draft, mode, specQuery.data)
  }, [draft, mode, specQuery.data])

  // -- Phase 1 -----------------------------------------------------------
  if (phase === 'find' || draft === null) {
    return (
      <Stack gap="lg" p="xl" maw={1100} mx="auto">
        <Title order={3}>{t('configurator.find.title')}</Title>

        <Group gap="xs">
          <Text size="sm" fw={500}>
            {t('configurator.mode.label')}
          </Text>
          <SegmentedControl
            size="xs"
            data={configuratorModeOptions(t)}
            value={mode}
            onChange={(v) => setMode(v as ConfiguratorMode)}
            aria-label={t('configurator.mode.label')}
          />
        </Group>

        <TextInput
          label={t('configurator.find.idLabel')}
          description={t('configurator.find.idHint')}
          placeholder="WVWZZZ..."
          value={vinInput}
          onChange={(e) => setVinInput(e.currentTarget.value)}
          maxLength={17}
          w={360}
        />

        <Group>
          <Button
            variant="light"
            leftSection={<PencilRuler size={16} />}
            onClick={startManual}
          >
            {t('configurator.find.manual')}
          </Button>
        </Group>

        <Text fw={600} size="sm">
          {t('configurator.find.browse')}
        </Text>
        <CatalogueBrowseGrid mode={mode} onSelectVariant={startFromVariant} />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {t('common.cancel')}
          </Button>
        </Group>
      </Stack>
    )
  }

  // -- Phase 2 -----------------------------------------------------------
  return (
    <Stack gap="md" p="xl" maw={900} mx="auto" pb={96}>
      <Group justify="space-between">
        <Title order={3}>
          {[draft.brandDisplayName, draft.variantName].filter(Boolean).join(' ') ||
            t('configurator.configure.title')}
        </Title>
        <Button variant="subtle" size="xs" onClick={() => setPhase('find')}>
          {t('configurator.configure.back')}
        </Button>
      </Group>

      <DetailTabs
        tabs={[
          { id: 'specification', label: t('configurator.sections.specification') },
          { id: 'options', label: t('configurator.sections.options') },
          { id: 'colour', label: t('configurator.sections.colour') },
          { id: 'images', label: t('configurator.sections.images') },
          { id: 'summary', label: t('configurator.sections.summary') },
        ]}
        activeTab={section}
        onTabChange={(id) => setSection(id as Section)}
      />

      {error && <Alert color="red">{error}</Alert>}

      {section === 'specification' && (
        <Stack gap="lg">
          {mode === 'record' && (
            <Group grow>
              <TextInput
                type="date"
                label={t('configurator.summary.firstRegistration')}
                value={draft.firstRegistrationDate}
                onChange={(e) => setDraft({ ...draft, firstRegistrationDate: e.currentTarget.value })}
              />
              <NumberInput
                label={t('configurator.summary.mileage')}
                value={draft.mileageKm === '' ? '' : Number(draft.mileageKm)}
                onChange={(v) => setDraft({ ...draft, mileageKm: v === '' ? '' : String(v) })}
                min={0}
              />
            </Group>
          )}
          {draft.source === 'manual' && (
            <Group grow>
              <TextInput
                label={t('catalogueBrowse.columns.brand')}
                value={draft.brandDisplayName ?? ''}
                onChange={(e) => setDraft({ ...draft, brandDisplayName: e.currentTarget.value })}
              />
              <TextInput
                label={t('catalogueBrowse.columns.modelGroup')}
                value={draft.modelGroupName ?? ''}
                onChange={(e) => setDraft({ ...draft, modelGroupName: e.currentTarget.value })}
              />
              <TextInput
                label={t('catalogueBrowse.columns.variant')}
                value={draft.variantName ?? ''}
                onChange={(e) => setDraft({ ...draft, variantName: e.currentTarget.value })}
              />
            </Group>
          )}

          {draft.source === 'manual' && (
            <Alert color="gray" icon={<Info size={16} />}>
              {t('configurator.find.unverifiedNote')}
            </Alert>
          )}

          {SPEC_FIELD_GROUPS.map((group) => (
            <Stack key={group.titleKey} gap="xs">
              <Text fw={600} size="sm">
                {t(group.titleKey)}
              </Text>
              <Group grow align="flex-start" wrap="wrap">
                {group.fields.map((f) => {
                  const label = t(specFieldLabelKey(f.key as string))
                  const marked = draft.overridden.has(f.key as string)
                  const value = (draft.spec[f.key] ?? '') as string | number
                  const withMark = marked ? `${label} • ${t('configurator.spec.overridden')}` : label
                  if (f.kind === 'ref') {
                    return (
                      <Select
                        key={f.key as string}
                        label={withMark}
                        data={(refLabels.data?.[f.listCode!] ?? []).map((r) => ({
                          value: r.valueCode,
                          label: r.label,
                        }))}
                        value={(draft.spec[f.key] as string) ?? null}
                        onChange={(v) => setSpecField(f.key, v)}
                        clearable
                        w={200}
                      />
                    )
                  }
                  if (f.kind === 'text') {
                    return (
                      <TextInput
                        key={f.key as string}
                        label={withMark}
                        value={value as string}
                        onChange={(e) => setSpecField(f.key, e.currentTarget.value)}
                        w={200}
                      />
                    )
                  }
                  return (
                    <NumberInput
                      key={f.key as string}
                      label={withMark}
                      value={value === '' ? '' : Number(value)}
                      onChange={(v) => setSpecField(f.key, v === '' ? null : Number(v))}
                      decimalScale={f.kind === 'decimal' ? 1 : 0}
                      w={200}
                    />
                  )
                })}
              </Group>
            </Stack>
          ))}
        </Stack>
      )}

      {section === 'options' && (
        <OptionsTab
          spec={specQuery.data}
          isLoading={specQuery.isLoading}
          isError={specQuery.isError}
          isManual={draft.catalogueVariantId == null}
          mode={mode}
          selected={draft.selectedOptions}
          onToggle={handleOptionToggle}
          onAddPackageContents={handleAddPackageContents}
          onUpdateFeatures={handleUpdateFeatures}
          optionGroupLabel={optionGroupLabel}
          equipmentFeatureLabel={equipmentFeatureLabel}
          equipmentFeatureOptions={equipmentFeatureOptions}
        />
      )}

      {section === 'colour' && (
        <ColourWheelsTab
          spec={specQuery.data}
          mode={mode}
          exteriorColour={draft.exteriorColour}
          interiorColour={draft.interiorColour}
          exteriorColourSurcharge={draft.exteriorColourSurcharge}
          interiorColourSurcharge={draft.interiorColourSurcharge}
          wheels={draft.wheels}
          wheelsSurcharge={draft.wheelsSurcharge}
          onChange={(patch) => setDraft({ ...draft, ...patch })}
        />
      )}

      {section === 'images' && (
        <ImagesTab spec={specQuery.data} isLoading={specQuery.isLoading} isManual={draft.catalogueVariantId == null} />
      )}

      {section === 'summary' && readModel && (
        <Stack gap="md">
          <Textarea
            label={t('configurator.summary.notesLabel')}
            value={draft.notes}
            onChange={(e) => setDraft({ ...draft, notes: e.currentTarget.value })}
            autosize
            minRows={2}
          />
          <ConfigurationSummaryCard configuration={readModel} />
        </Stack>
      )}

      <StickyActionFooter
        primaryAction={
          <Button onClick={() => void save()} loading={saving}>
            {configurationId ? t('configurator.save') : t('configurator.saveNew')}
          </Button>
        }
      />
    </Stack>
  )
}

// --- helpers --------------------------------------------------------

function useReferenceLabels() {
  const { i18n } = useTranslation()
  const lang = (i18n.language || 'de').slice(0, 2)
  return useQuery({
    queryKey: ['configurator-ref-labels', OPTION_REF_LIST_CODES],
    queryFn: async () => {
      const key = `label${lang.charAt(0).toUpperCase()}${lang.slice(1)}` as
        | 'labelDe'
        | 'labelFr'
        | 'labelIt'
        | 'labelEn'
      const entries = await Promise.all(
        OPTION_REF_LIST_CODES.map(async (code) => {
          try {
            const page = await api.get<ReferenceValuePage>(`/reference-data/${code}?limit=200`)
            return [code, page.items.map((v) => ({ valueCode: v.valueCode, label: v[key] || v.valueCode }))] as const
          } catch {
            return [code, []] as const
          }
        }),
      )
      return Object.fromEntries(entries) as Record<string, { valueCode: string; label: string }[]>
    },
  })
}

function pickSpec(spec: VehicleSpecBlockRead, keys: Set<string>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) out[k] = (spec as Record<string, unknown>)[k]
  return out
}

function nonEmptySpec(spec: VehicleSpecBlockRead): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(spec)) if (v !== null && v !== undefined && v !== '') out[k] = v
  return out
}

function toConfigurationOptionInput(option: CatalogueOptionRead): ConfigurationOptionInput {
  return {
    variantOptionId: option.id,
    optionCode: option.optionCode,
    description: option.description,
    optionGroup: option.optionGroup,
    price: option.price,
    isIncluded: option.isIncluded,
    isPackage: option.isPackage,
    selected: true,
    equipmentFeatures: [...option.equipmentFeatures],
  }
}

/** Every included option (always) plus whatever the advisor explicitly
 * selected — see `OptionsTabProps.selected`'s own doc comment on why the
 * draft only ever tracks explicit overrides. */
function finalOptionsPayload(
  draft: Draft,
  spec: CatalogueSpecificationRead | undefined,
): ConfigurationOptionInput[] {
  if (!spec) return []
  return spec.options
    .filter((o) => o.isIncluded || draft.selectedOptions.get(o.id)?.selected === true)
    .map((o) => draft.selectedOptions.get(o.id) ?? toConfigurationOptionInput(o))
}

function previewOptions(draft: Draft, spec: CatalogueSpecificationRead | undefined): ConfigurationOptionRead[] {
  return finalOptionsPayload(draft, spec).map((o, sequence) => ({
    id: o.variantOptionId ?? `preview-${sequence}`,
    sequence,
    variantOptionId: o.variantOptionId ?? null,
    optionCode: o.optionCode ?? null,
    description: o.description,
    optionGroup: o.optionGroup ?? null,
    price: o.price != null ? String(o.price) : null,
    isIncluded: o.isIncluded ?? false,
    isPackage: o.isPackage ?? false,
    selected: o.selected ?? true,
    equipmentFeatures: o.equipmentFeatures ?? [],
  }))
}

/** A client-side `ConfigurationRead` shape for the summary card preview,
 * before the draft is saved. */
function previewRead(draft: Draft, mode: ConfiguratorMode, spec: CatalogueSpecificationRead | undefined): ConfigurationRead {
  return {
    id: 'draft',
    tenantId: 'draft',
    source: draft.source,
    mode,
    catalogueMatchStatus: draft.source === 'provider' ? 'matched' : 'unverified',
    matchMethod: draft.source === 'provider' ? 'catalogue_browse' : 'manual',
    catalogueVariantId: draft.catalogueVariantId,
    catalogueVariantLabel: null,
    vehicleId: null,
    vehicleLabel: null,
    vin: draft.vin || null,
    stammnummer: null,
    typeApprovalNumber: null,
    firstRegistrationDate: draft.firstRegistrationDate || null,
    licencePlate: null,
    mileageKm: draft.mileageKm ? Number(draft.mileageKm) : null,
    brandDisplayName: draft.brandDisplayName,
    modelGroupName: draft.modelGroupName,
    variantName: draft.variantName,
    exteriorColour: draft.exteriorColour || null,
    interiorColour: draft.interiorColour || null,
    exteriorColourSurcharge: draft.exteriorColourSurcharge || null,
    interiorColourSurcharge: draft.interiorColourSurcharge || null,
    wheels: draft.wheels || null,
    wheelsSurcharge: draft.wheelsSurcharge || null,
    fuelType: null,
    bodyStyle: null,
    drivetrain: null,
    transmission: null,
    vehicleKind: null,
    spec: draft.spec,
    overriddenFields: [...draft.overridden],
    options: previewOptions(draft, spec),
    notes: draft.notes || null,
    version: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
}
