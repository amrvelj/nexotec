import { memo, useCallback, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  CloseButton,
  Group,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ChfInput, DetailTabs, StickyActionFooter } from '@nexotec/ui-kit'
import { api } from '../../api/client'
import { ConfigurationSummaryCard } from './ConfigurationSummaryCard'
import { OptionsTab } from './OptionsTab'
import { ColourWheelsTab } from './ColourWheelsTab'
import { ImagesTab } from './ImagesTab'
import { DetailStateNotice } from './DetailStateNotice'
import type { IdentifiedStart } from './IdentificationPanel'
import { ConfigurationResyncPanel } from './ConfigurationResyncPanel'
import { ConfiguratorFindPhase } from './ConfiguratorFindPhase'
import { CodedSelect, ProviderCodedFields } from './ConfiguratorCodedFields'
import { HostCommitError } from './hostCommitError'
import {
  draftFromRead,
  draftFromVariant,
  emptyManualDraft,
  finalOptionsPayload,
  fullSpec,
  pickSpec,
  previewRead,
  toConfigurationOptionInput,
  withObserved,
  type Draft,
  type Phase,
  type Section,
} from './configuratorDraft'
import {
  codedValueRetiredOf,
  refLabelOf,
  specRefOptionsOf,
  specRefRetiredOf,
  specRefWillBeSent,
  type RefLabelRows,
  type SpecFieldGroupDef,
} from './configuratorFieldRules'
import type { ConfiguratorOverlayProps } from './configuratorOverlayProps'
import { useCodedFieldValues, useReferenceLabels } from './useConfiguratorReferenceData'
import { LABEL_FIELD, resolveLanguage } from '../../hooks/useReferenceValueOptions'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { isChassisNumber, normalisedVin, normalisedVinOrChassisNumber, vinRefusalKey } from '../../utils/vin'
import { useFieldHint } from '../../hooks/useFieldHint'
import {
  CODED_FIELDS,
  SPEC_FIELD_GROUPS,
  specFieldLabelKey,
  type CodedFieldKey,
  type ConfiguratorMode,
} from '../../configurationOptions'
import type {
  CatalogueOptionRead,
  CatalogueSpecificationRead,
  CatalogueVariantRead,
  ConfigurationRead,
  VehicleMdmRead,
  VehicleSpecBlockRead,
} from '../../api/types'

export type { ConfiguratorOverlayProps, HostOwnedVin } from './configuratorOverlayProps'

// KAN-245 — the spec block's reference fields (engine cycle, vehicle class,
// valuation classification, emission standard, consumption norm).
const SPEC_REF_FIELDS = SPEC_FIELD_GROUPS.flatMap((g) => g.fields).filter((f) => f.kind === 'ref')

const ALL_MODES: ConfiguratorMode[] = ['build', 'record']

/**
 * The two-phase configurator (ADR-059 overlay, ADR-068 entity). Phase 1
 * gates phase 2 (options can't be offered before a car is known); nothing
 * inside phase 2 is sequenced — **not a wizard**. One component, both
 * modes. No standalone route: a host renders this inside an
 * `OverlayProvider` layer (C-F wires the real hosts).
 */
export function ConfiguratorOverlay({
  allowedModes = ALL_MODES,
  initialMode,
  existing = null,
  initialVin = '',
  onCommitted,
  hostOwnsVin,
  onClose,
}: ConfiguratorOverlayProps) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const [phase, setPhase] = useState<Phase>(existing ? 'configure' : 'find')
  const [mode, setMode] = useState<ConfiguratorMode>(
    existing?.mode ?? (initialMode && allowedModes.includes(initialMode) ? initialMode : allowedModes[0]),
  )
  const [section, setSection] = useState<Section>('specification')
  const [draft, setDraft] = useState<Draft | null>(existing ? draftFromRead(existing) : null)
  const [vinInput, setVinInput] = useState(initialVin)
  // The host's VIN prefills the first car only; Back starts the next one empty.
  const [identificationPrefill, setIdentificationPrefill] = useState(initialVin)
  const [configurationId, setConfigurationId] = useState<string | null>(existing?.id ?? null)
  const [resyncOpen, setResyncOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-557: the VIN field's own refusal when the host owns the VIN.
  const [vinError, setVinError] = useState<string | null>(null)
  // KAN-266: the first save's POST carries one key per submission, so a
  // retry after a failure (a response lost on the way back) gets the
  // configuration it created instead of a second one. A draft changed after
  // that failure is another request and gets a new key. The overlay mounts
  // afresh on every opening.
  const createKey = useIdempotencyKey()

  const refLabels = useReferenceLabels()
  const codedValues = useCodedFieldValues()

  const startFromVariant = (variant: CatalogueVariantRead) => {
    const d = draftFromVariant(variant)
    // An input that matched nothing is kept as the VIN when it is one — by
    // the API's own rule (KAN-338), so a save never sends a VIN it refuses.
    const typed = normalisedVin(vinInput)
    if (typed) d.vin = typed
    setDraft(d)
    setPhase('configure')
  }

  const startManual = () => {
    const d = emptyManualDraft()
    // As above: kept only when the API would accept it (KAN-338).
    const typed = normalisedVin(vinInput)
    if (typed) d.vin = typed
    setDraft(d)
    setPhase('configure')
  }

  // KAN-261 — Back from phase 2 starts a genuinely new configuration. Once
  // the first save has happened, `configurationId` would otherwise turn the
  // next car's save into a PATCH of the configuration already saved (one
  // record describing two cars, FR-C-11). The saved one stays as it is;
  // only this overlay session lets go of it. Back is disabled while a save
  // is in flight: its result would otherwise put the old id back. The typed
  // identification input goes too — the panel remounts empty, and a stale
  // VIN must not ride into the next car's draft.
  const backToFind = () => {
    setConfigurationId(null)
    setVinInput('')
    setIdentificationPrefill('')
    setCurrentVersion(0)
    setDraft(null)
    setResyncOpen(false)
    setSection('specification')
    setError(null)
    setPhase('find')
  }

  const startFromIdentification = (start: IdentifiedStart) => {
    setDraft(withObserved(draftFromVariant(start.variant), start))
    setPhase('configure')
  }

  // KAN-335 — stable setters: the memoised spec groups and coded selects
  // below re-render only when their own values change.
  const setSpecField = useCallback((key: keyof VehicleSpecBlockRead, value: number | string | null) => {
    setDraft((prev) => {
      if (!prev) return prev
      const overridden = new Set(prev.overridden)
      if (prev.source === 'provider') overridden.add(key as string)
      return { ...prev, spec: { ...prev.spec, [key]: value === '' ? null : value }, overridden }
    })
  }, [])

  // `allowDeselect={false}`: a pick never comes back null.
  const setCodedField = useCallback((key: CodedFieldKey, value: string) => {
    setDraft((prev) => (prev ? { ...prev, coded: { ...prev.coded, [key]: value } } : prev))
  }, [])

  // KAN-43 (C-E) — the catalogue's own options/colours/tyres/images for
  // this variant. Never fetched for a manual configuration (no variant to
  // ask about) — see OptionsTab's own "no catalogue data" state.
  const specQuery = useQuery({
    queryKey: ['catalogue-variant-specification', draft?.catalogueVariantId],
    queryFn: () => api.get<CatalogueSpecificationRead>(`/catalogue/variants/${draft!.catalogueVariantId}/specification`),
    enabled: draft?.catalogueVariantId != null,
    // KAN-211 (ADR-123): opening the car is what loads its details at the
    // provider, so this read is asked once per open — no refetch on window
    // focus (that would re-ask an «unavailable» detail on every tab switch,
    // a billed request) and no retry (a failure is a state, not an error).
    refetchOnWindowFocus: false,
    retry: false,
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

  // The rules behind these labels and flags are the pure functions in
  // `configuratorFieldRules.ts` (KAN-335), shared by the memoised fields and save().
  const optionGroupLabel = (code: string) => refLabelOf(refLabels.data, 'option_group', code)
  const equipmentFeatureLabel = (code: string) => refLabelOf(refLabels.data, 'equipment_feature', code)
  // A picker offers active values only — the full list is for labelling
  // what is already stored.
  const equipmentFeatureOptions = (refLabels.data?.equipment_feature ?? [])
    .filter((r) => r.active)
    .map((r) => ({ value: r.valueCode, label: r.label }))
  // KAN-211 — the colour types the advisor names by hand (`ColourWheelsTab`).
  // A retired stored value stays visible, disabled, labelled (KAN-96's pattern).
  const colourTypeOptionsOf = (listCode: string, current: string) => ({
    options: specRefOptionsOf(refLabels.data, listCode, current || null),
    isError: refLabels.data !== undefined && refLabels.data[listCode] === null,
  })
  const exteriorColourTypeOptions = colourTypeOptionsOf('colour_type', draft?.exteriorColourType ?? '')
  const interiorColourTypeOptions = colourTypeOptionsOf('interior_colour_type', draft?.interiorColourType ?? '')
  const codedLabelField = LABEL_FIELD[resolveLanguage(i18n.language)]

  const save = async () => {
    if (!draft) return
    if (draft.source === 'manual') {
      // Anto, 2026-10-12 — a manual configuration needs all five coded
      // fields; the API refuses a create without them (422
      // `coded_fields_required`), so nothing is sent until they are picked.
      const missing = CODED_FIELDS.filter((f) => draft.coded[f.key] == null)
      if (missing.length > 0) {
        setError(t('configurator.spec.codedRequiredSaveError', { fields: missing.map((f) => t(f.labelKey)).join(', ') }))
        return
      }
    }
    const retired = [
      ...(draft.source === 'manual'
        ? CODED_FIELDS.filter((f) => codedValueRetiredOf(codedValues.data, f.listCode, draft.coded[f.key])).map((f) =>
            t(f.labelKey),
          )
        : []),
      ...SPEC_REF_FIELDS.filter(
        (f) =>
          specRefWillBeSent(draft.source, draft.overridden, f.key as string) &&
          specRefRetiredOf(refLabels.data, f.listCode!, (draft.spec[f.key] as string) ?? null),
      ).map((f) => t(specFieldLabelKey(f.key as string))),
      // KAN-211: the colour types are always sent and refused when retired, like the rest.
      ...(specRefRetiredOf(refLabels.data, 'colour_type', draft.exteriorColourType || null) ? [t('configurator.colour.exteriorType')] : []),
      ...(specRefRetiredOf(refLabels.data, 'interior_colour_type', draft.interiorColourType || null) ? [t('configurator.colour.interiorType')] : []),
    ]
    if (retired.length > 0) {
      setError(t('configurator.spec.retiredSaveError', { fields: retired.join(', ') }))
      return
    }
    // KAN-557: on a saved configuration whose host owns the VIN, the VIN
    // never rides in the PATCH. A changed one goes to the host after the
    // saves below; an emptied or invalid one is refused here, nothing sent.
    const vinOwner = hostOwnsVin && configurationId !== null ? hostOwnsVin : null
    let vinForHost: string | null = null
    if (vinOwner && !vinOwner.readOnlyReason) {
      // KAN-535: a pre-1981 car bought under its chassis number takes a
      // corrected chassis number (or its real VIN) through the host too.
      const normaliseForHost = isChassisNumber(savedVin) ? normalisedVinOrChassisNumber : normalisedVin
      const unchanged = draft.vin === (savedVin ?? '') || normaliseForHost(draft.vin) === (savedVin ?? null)
      if (draft.vin.trim() === '' && savedVin) {
        setVinError(vinOwner.clearRefused)
        setSection('specification') // where the field is
        return
      }
      if (!unchanged && draft.vin.trim() !== '') {
        vinForHost = normaliseForHost(draft.vin)
        if (vinForHost === null) {
          setVinError(t(isChassisNumber(savedVin) ? 'stockDetail.vinCorrection.invalidChassisNumber' : 'common.invalidVin'))
          setSection('specification')
          return
        }
      }
    }
    setVinError(null)
    setSaving(true)
    setError(null)
    try {
      // KAN-247 — a cleared field goes out as null, which the API reads as
      // "clear it"; a field left out is left alone.
      const specPayload = draft.source === 'provider'
        ? pickSpec(draft.spec, draft.overridden)
        : fullSpec(draft.spec)

      // KAN-338: the API refuses a malformed VIN (422). A configuration saved
      // before that rule may hold one — so an unchanged, malformed stored VIN
      // is left out of the PATCH (left as stored) instead of blocking every
      // save that does not touch it; the VIN field (KAN-340) corrects or
      // clears it. An empty VIN still goes out as null, which clears it and
      // the vehicle link (KAN-247, KAN-326).
      const keepStoredVin =
        configurationId !== null && draft.vin !== '' && draft.vin === savedVin && normalisedVin(draft.vin) === null
      const body: Record<string, unknown> = {
        mode,
        ...(keepStoredVin || vinOwner ? {} : { vin: draft.vin || null }),
        firstRegistrationDate: draft.firstRegistrationDate || null,
        mileageKm: draft.mileageKm ? Number(draft.mileageKm) : null,
        exteriorColour: draft.exteriorColour || null,
        interiorColour: draft.interiorColour || null,
        exteriorColourType: draft.exteriorColourType || null,
        interiorColourType: draft.interiorColourType || null,
        // KAN-43 (C-E) / FR-C-07, FR-C-08 — a surcharge only means anything
        // in build mode (a new-vehicle price build-up); record mode never
        // shows the inputs (see the 'colour' section below), so nothing
        // here forces record-mode drafts to carry a stray non-null value.
        exteriorColourSurcharge: draft.exteriorColourSurcharge || null,
        interiorColourSurcharge: draft.interiorColourSurcharge || null,
        wheels: draft.wheels || null,
        wheelsSurcharge: draft.wheelsSurcharge || null,
        notes: draft.notes || null,
        licencePlate: draft.licencePlate || null,
        stammnummer: draft.stammnummer || null,
        typeApprovalNumber: draft.typeApprovalNumber || null,
        // KAN-96 — only a manual configuration sends its coded fields; a
        // provider one's come from the catalogue and are not the advisor's.
        ...(draft.source === 'manual' ? draft.coded : {}),
      }

      let config: ConfigurationRead
      const isFirstSave = configurationId === null
      if (isFirstSave) {
        const createBody = {
          ...body,
          source: draft.source,
          matchMethod: draft.matchMethod ?? (draft.source === 'provider' ? 'catalogue_browse' : 'manual'),
          catalogueVariantId: draft.catalogueVariantId,
          confirmedBestMatchCode: draft.confirmedBestMatchCode,
          brandDisplayName: draft.brandDisplayName || null,
          modelGroupName: draft.modelGroupName || null,
          variantName: draft.variantName || null,
          spec: specPayload,
        }
        config = await api.post<ConfigurationRead>('/configurations', createBody, createKey.headers(createBody))
        createKey.renew()
        setConfigurationId(config.id)
      } else {
        config = await api.patch<ConfigurationRead>(
          `/configurations/${configurationId}`,
          {
            ...body,
            // The make / model / variant names are the advisor's only on a
            // manual configuration (the only one that shows them) — and,
            // like every other field, cleared when emptied (KAN-247).
            ...(draft.source === 'manual'
              ? {
                  brandDisplayName: draft.brandDisplayName || null,
                  modelGroupName: draft.modelGroupName || null,
                  variantName: draft.variantName || null,
                }
              : {}),
            spec: specPayload,
          },
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
      setSavedVin(config.vin)
      setLinkedVehicle(config.vehicleId ? { id: config.vehicleId, label: config.vehicleLabel } : null)

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

      if (vinForHost !== null && vinOwner) {
        // The host's correction rewrites the configuration too (its VIN and
        // version): read it back before committing, so a later save sends
        // the version the server holds.
        await vinOwner.correctVin(vinForHost)
        config = await api.get<ConfigurationRead>(`/configurations/${config.id}`)
        setCurrentVersion(config.version)
        setSavedVin(config.vin)
        setLinkedVehicle(config.vehicleId ? { id: config.vehicleId, label: config.vehicleLabel } : null)
        setDraft((current) => (current ? { ...current, vin: config.vin ?? '' } : current))
      }

      await onCommitted(config)
    } catch (err) {
      // KAN-351 — a VIN the API refuses is said in the overlay's error, in
      // the user's language.
      const vinRefusal = vinRefusalKey(err)
      if (vinRefusal) setError(t(vinRefusal))
      else setError(err instanceof HostCommitError ? err.message : t('configurator.saveError'))
    } finally {
      setSaving(false)
    }
  }

  const [currentVersion, setCurrentVersion] = useState(existing?.version ?? 0)
  // The VIN the server holds for this configuration (KAN-338, see save()).
  const [savedVin, setSavedVin] = useState<string | null>(existing?.vin ?? null)
  // KAN-340: the vehicle-mdm record the saved configuration is linked to —
  // the server's answer, which follows the VIN (KAN-326). It is named as its
  // own detail header names it: the vehicle number (`vehicleLabel`, rule 2's
  // label) and its VIN, read from the record itself.
  const [linkedVehicle, setLinkedVehicle] = useState<{ id: string; label: string | null } | null>(
    existing?.vehicleId ? { id: existing.vehicleId, label: existing.vehicleLabel } : null,
  )
  const linkedVehicleQuery = useQuery({
    queryKey: ['vehicle-mdm', linkedVehicle?.id],
    queryFn: () => api.get<VehicleMdmRead>(`/vehicle-mdm/${linkedVehicle?.id}`),
    enabled: linkedVehicle != null,
    retry: false,
  })

  const readModel: ConfigurationRead | null = useMemo(() => {
    if (!draft) return null
    return previewRead(draft, mode, specQuery.data)
  }, [draft, mode, specQuery.data])

  // -- Phase 1 -----------------------------------------------------------
  if (phase === 'find' || draft === null) {
    return (
      <ConfiguratorFindPhase
        allowedModes={allowedModes}
        mode={mode}
        setMode={setMode}
        error={error}
        startFromIdentification={startFromIdentification}
        onReuse={(configuration) => {
          setError(null)
          Promise.resolve(onCommitted(configuration)).catch((err: unknown) =>
            setError(err instanceof HostCommitError ? err.message : t('configurator.saveError')),
          )
        }}
        setVinInput={setVinInput}
        identificationPrefill={identificationPrefill}
        startManual={startManual}
        startFromVariant={startFromVariant}
        onClose={onClose}
      />
    )
  }

  // -- Phase 2 -----------------------------------------------------------
  const hostVin = hostOwnsVin && configurationId !== null ? hostOwnsVin : null
  return (
    <Stack gap="md" p="xl" maw={900} mx="auto" pb={96}>
      <Group justify="space-between">
        <Title order={3}>
          {[draft.brandDisplayName, draft.variantName].filter(Boolean).join(' ') ||
            t('configurator.configure.title')}
        </Title>
        <Group gap="xs">
          {configurationId && draft.catalogueVariantId && (
            <Button variant="default" size="xs" onClick={() => setResyncOpen(true)}>
              {t('configurator.resync.open')}
            </Button>
          )}
          {!existing && (
            <Button variant="subtle" size="xs" onClick={backToFind} disabled={saving}>
              {t('configurator.configure.back')}
            </Button>
          )}
        </Group>
      </Group>

      {configurationId && resyncOpen && (
        <ConfigurationResyncPanel
          configurationId={configurationId}
          version={currentVersion}
          onClose={() => setResyncOpen(false)}
          onApplied={(config) => {
            setDraft(draftFromRead(config))
            setCurrentVersion(config.version)
            setResyncOpen(false)
          }}
        />
      )}

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
          {/* KAN-340 (FR-C-11, FR-C-16): the VIN is the advisor's to correct or
              clear. A cleared VIN goes out as null (KAN-247) and, on a saved
              configuration, drops the vehicle link with it (KAN-326). */}
          <Group grow align="flex-start">
            <TextInput
              {...hint(
                t('configurator.vehicleLink.vin'),
                hostVin ? (hostVin.readOnlyReason ?? hostVin.hint) : t('configurator.vehicleLink.vinHint'),
              )}
              value={draft.vin}
              readOnly={Boolean(hostVin?.readOnlyReason)}
              error={vinError}
              onChange={(e) => {
                setDraft({ ...draft, vin: e.currentTarget.value })
                if (vinError) setVinError(null)
              }}
              rightSection={
                // A VIN the host owns is never cleared here (KAN-557).
                draft.vin !== '' && !hostVin ? (
                  <CloseButton
                    size="sm"
                    aria-label={t('configurator.vehicleLink.clearVin')}
                    onClick={() => setDraft({ ...draft, vin: '' })}
                  />
                ) : null
              }
            />
            {configurationId !== null && (
              <Stack gap={2} data-testid="configurator-linked-vehicle">
                <Text size="sm" fw={500}>
                  {t('configurator.vehicleLink.linkedVehicle')}
                </Text>
                {linkedVehicle ? (
                  <>
                    {/* UI/UX Specification, Typography: mono is mandatory for identifiers. */}
                    {linkedVehicleQuery.data?.vehicleNumber ?? linkedVehicle.label ? (
                      <Text size="sm" ff="monospace">
                        {linkedVehicleQuery.data?.vehicleNumber ?? linkedVehicle.label}
                      </Text>
                    ) : (
                      <Text size="sm" c="dimmed">
                        {t('configurator.vehicleLink.unknownVehicle')}
                      </Text>
                    )}
                    {linkedVehicleQuery.data?.vin && (
                      <Text size="xs" c="dimmed" ff="monospace">
                        {linkedVehicleQuery.data.vin}
                      </Text>
                    )}
                  </>
                ) : (
                  <Text size="sm" c="dimmed">
                    {t('configurator.vehicleLink.noVehicle')}
                  </Text>
                )}
              </Stack>
            )}
          </Group>
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

          {draft.source === 'manual' && (
            <Stack gap="xs">
              <Text fw={600} size="sm">
                {t('configurator.spec.groups.vehicleType')}
              </Text>
              <Group grow align="flex-start" wrap="wrap">
                {CODED_FIELDS.map((f) => (
                  <CodedSelect
                    key={f.key}
                    field={f}
                    value={draft.coded[f.key]}
                    codedValues={codedValues.data}
                    labelField={codedLabelField}
                    onPick={setCodedField}
                  />
                ))}
              </Group>
            </Stack>
          )}

          {draft.source === 'provider' && <ProviderCodedFields coded={draft.coded} />}

          {SPEC_FIELD_GROUPS.map((group) => (
            <SpecFieldGroup
              key={group.titleKey}
              group={group}
              spec={draft.spec}
              overridden={draft.overridden}
              source={draft.source}
              refLabels={refLabels.data}
              onSpecChange={setSpecField}
            />
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
          exteriorColourType={draft.exteriorColourType}
          interiorColourType={draft.interiorColourType}
          exteriorColourTypeOptions={exteriorColourTypeOptions}
          interiorColourTypeOptions={interiorColourTypeOptions}
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
          {/* KAN-211 / KAN-212: the Energieetikette's load state, where the label is
           * read — nothing for a plain loaded label, the reason when the gearboxes
           * disagree on the class (`energy_label_ambiguous`). */}
          {specQuery.data && <DetailStateNotice detail="energyLabel" state={specQuery.data.details.energyLabel} />}
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

// --- memoised fields (KAN-335) -------------------------------------
//
// The draft lives in one state, so every keystroke re-renders the overlay.
// The spec groups and the coded fields are memoised so that a keystroke
// re-renders only the group it lands in, and a coded pick only its own
// select (`ConfiguratorOverlay.renderCount.render.test.tsx`). What is shown,
// validated and saved is unchanged.

interface SpecFieldGroupProps {
  group: SpecFieldGroupDef
  spec: VehicleSpecBlockRead
  overridden: Set<string>
  source: Draft['source']
  refLabels: RefLabelRows | undefined
  onSpecChange: (key: keyof VehicleSpecBlockRead, value: number | string | null) => void
}

/** Equal when nothing this group shows changed: its own fields' values and
 * override marks, the lists it labels from, the setter. Another group's
 * keystroke leaves all of these as they were. */
function specFieldGroupPropsEqual(prev: SpecFieldGroupProps, next: SpecFieldGroupProps): boolean {
  if (
    prev.group !== next.group ||
    prev.source !== next.source ||
    prev.refLabels !== next.refLabels ||
    prev.onSpecChange !== next.onSpecChange
  ) {
    return false
  }
  return prev.group.fields.every(
    (f) =>
      prev.spec[f.key] === next.spec[f.key] &&
      prev.overridden.has(f.key as string) === next.overridden.has(f.key as string),
  )
}

const SpecFieldGroup = memo(function SpecFieldGroup({
  group,
  spec,
  overridden,
  source,
  refLabels,
  onSpecChange,
}: SpecFieldGroupProps) {
  const { t } = useTranslation()
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        {t(group.titleKey)}
      </Text>
      <Group grow align="flex-start" wrap="wrap">
        {group.fields.map((f) => {
          const label = t(specFieldLabelKey(f.key as string))
          const marked = overridden.has(f.key as string)
          const value = (spec[f.key] ?? '') as string | number
          const withMark = marked ? `${label} • ${t('configurator.spec.overridden')}` : label
          if (f.kind === 'ref') {
            const current = (spec[f.key] as string) ?? null
            return (
              <Select
                key={f.key as string}
                label={withMark}
                data={specRefOptionsOf(refLabels, f.listCode!, current)}
                value={current}
                onChange={(v) => onSpecChange(f.key, v)}
                error={
                  refLabels?.[f.listCode!] === null
                    ? t('configurator.spec.listUnavailable')
                    : specRefWillBeSent(source, overridden, f.key as string) &&
                        specRefRetiredOf(refLabels, f.listCode!, current)
                      ? t('configurator.spec.retired')
                      : undefined
                }
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
                onChange={(e) => onSpecChange(f.key, e.currentTarget.value)}
                w={200}
              />
            )
          }
          if (f.kind === 'chf') {
            return (
              <ChfInput
                key={f.key as string}
                label={withMark}
                value={value === '' ? '' : Number(value)}
                onChange={(v) => onSpecChange(f.key, v === '' ? null : Number(v))}
                w={200}
              />
            )
          }
          return (
            <NumberInput
              key={f.key as string}
              label={withMark}
              value={value === '' ? '' : Number(value)}
              onChange={(v) => onSpecChange(f.key, v === '' ? null : Number(v))}
              decimalScale={f.kind === 'decimal' ? 1 : 0}
              w={200}
            />
          )
        })}
      </Group>
    </Stack>
  )
}, specFieldGroupPropsEqual)
