import { useEffect, useState } from 'react'
import { Alert, Badge, Button, Checkbox, CloseButton, Group, Select, Stack, Text } from '@mantine/core'
import { AlertTriangle, Info, Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatCurrencyChf } from '../../utils/format'
import type {
  CatalogueOptionRead,
  CatalogueOptionRelationRead,
  CatalogueSpecificationRead,
  ConfigurationOptionInput,
} from '../../api/types'
import type { ConfiguratorMode } from '../../configurationOptions'

// FR-C-06's own phrasing example: "contained in Pack Family · excludes
// Sportsitze · CHF 400 in combination with Klimaautomat · becomes standard
// with …" — one i18n template per `option_relation_type` code (the
// platform-seeded canonical list, alembic platform/a3d9c1e58f27). `contains`
// gets a distinct reverse key (`containedIn`) for the child's own row; every
// other relation reads naturally from either side, so the same template is
// reused with whichever option is the "other" one in that direction.
const RELATION_I18N_KEY: Record<string, string> = {
  excludes: 'excludes',
  contains: 'contains',
  becomes_standard_with: 'becomesStandardWith',
  price_in_combination_with: 'priceInCombinationWith',
  not_with: 'notWith',
  only_with: 'onlyWith',
  not_in_combination_with: 'notInCombinationWith',
  only_in_combination_with: 'onlyInCombinationWith',
}

function pairKey(aId: string, bId: string): string {
  return [aId, bId].sort().join('|')
}

export interface OptionsTabProps {
  /** `undefined` while the catalogue-specification query hasn't resolved. */
  spec: CatalogueSpecificationRead | undefined
  isLoading: boolean
  isError: boolean
  /** A manual (off-catalogue) configuration has no `catalogueVariantId`, so
   * there is nothing here to fetch — FR-C-07's "free text" affordance for
   * a hand-typed options list is a separate, smaller follow-up, not this
   * panel's job. */
  isManual: boolean
  mode: ConfiguratorMode
  /** Explicit overrides only, keyed by `variantOptionId`. An entry's own
   * `selected` flag (not merely its presence in the map) is what "checked"
   * means — unchecking a regular option flips `selected` to false rather
   * than deleting the entry, so a feature correction made while it was
   * checked survives a later re-check in the same session. An untouched
   * included option, or a regular option never checked at all, correctly
   * has no entry — both are derived fresh from `spec` at save time (see
   * `ConfiguratorOverlay.finalOptionsPayload`). */
  selected: Map<string, ConfigurationOptionInput>
  onToggle: (option: CatalogueOptionRead) => void
  onAddPackageContents: (options: CatalogueOptionRead[]) => void
  onUpdateFeatures: (variantOptionId: string, features: string[]) => void
  optionGroupLabel: (valueCode: string) => string | null
  equipmentFeatureLabel: (valueCode: string) => string
  equipmentFeatureOptions: { value: string; label: string }[]
}

export function OptionsTab({
  spec,
  isLoading,
  isError,
  isManual,
  mode,
  selected,
  onToggle,
  onAddPackageContents,
  onUpdateFeatures,
  optionGroupLabel,
  equipmentFeatureLabel,
  equipmentFeatureOptions,
}: OptionsTabProps) {
  const { t } = useTranslation()
  // Hooks are declared unconditionally, above every early return below
  // (rules of hooks) — the derived values they depend on tolerate a
  // `spec` that is still `undefined`.
  const [pendingPackageIds, setPendingPackageIds] = useState<Set<string>>(new Set())
  const [dismissedConflicts, setDismissedConflicts] = useState<Set<string>>(new Set())

  const options = spec?.options ?? []
  const relations = spec?.optionRelations ?? []
  const optionsById = new Map(options.map((o) => [o.id, o]))
  const outgoing = new Map<string, CatalogueOptionRelationRead[]>()
  const incoming = new Map<string, CatalogueOptionRelationRead[]>()
  for (const r of relations) {
    if (!outgoing.has(r.fromOptionId)) outgoing.set(r.fromOptionId, [])
    outgoing.get(r.fromOptionId)!.push(r)
    if (!incoming.has(r.toOptionId)) incoming.set(r.toOptionId, [])
    incoming.get(r.toOptionId)!.push(r)
  }

  // `isChecked` is the literal on-screen checkbox state; `isIncluded`
  // options never render one at all. `isSelected` is "part of the final
  // configuration", the one that conflict/package-offer logic cares about.
  const isChecked = (o: CatalogueOptionRead) => selected.get(o.id)?.selected === true
  const isSelected = (o: CatalogueOptionRead) => o.isIncluded || isChecked(o)
  const featuresFor = (o: CatalogueOptionRead) => selected.get(o.id)?.equipmentFeatures ?? o.equipmentFeatures

  const missingContentsFor = (pkg: CatalogueOptionRead, extraSelectedIds?: Set<string>): CatalogueOptionRead[] =>
    (outgoing.get(pkg.id) ?? [])
      .filter((r) => r.relationType === 'contains')
      .map((r) => optionsById.get(r.toOptionId))
      .filter((o): o is CatalogueOptionRead => !!o && !isSelected(o) && !extraSelectedIds?.has(o.id))

  // ADR-072 — a conflicting selection warns, it never blocks. Aggregated
  // into one banner ("build the warning well — clear, specific,
  // dismissible", the ticket's own words) but dismissal is tracked PER
  // PAIR, not over the aggregate: two independent conflicts dismissed
  // together must not both reappear just because one of them resolved,
  // and an identical pair that disappears and comes back (unchecked, then
  // rechecked) is a fresh occurrence, not something already acknowledged.
  const conflicts: { a: CatalogueOptionRead; b: CatalogueOptionRead; key: string }[] = []
  const seenPairs = new Set<string>()
  for (const r of relations) {
    if (r.relationType !== 'excludes') continue
    const a = optionsById.get(r.fromOptionId)
    const b = optionsById.get(r.toOptionId)
    if (!a || !b || !isSelected(a) || !isSelected(b)) continue
    const key = pairKey(a.id, b.id)
    if (seenPairs.has(key)) continue
    seenPairs.add(key)
    conflicts.push({ a, b, key })
  }
  const activeConflictKeys = conflicts.map((c) => c.key)
  const visibleConflicts = conflicts.filter((c) => !dismissedConflicts.has(c.key))

  useEffect(() => {
    const active = new Set(activeConflictKeys)
    setDismissedConflicts((prev) => {
      let changed = false
      const next = new Set<string>()
      for (const k of prev) {
        if (active.has(k)) next.add(k)
        else changed = true
      }
      return changed ? next : prev
    })
    // activeConflictKeys is a fresh array every render; joining it gives a
    // stable dependency that only actually changes when membership does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConflictKeys.join(',')])

  if (isManual) {
    return (
      <Alert color="gray" icon={<Info size={16} />} data-testid="options-no-catalogue-variant">
        {t('configurator.options.noCatalogueVariant')}
      </Alert>
    )
  }

  if (isError) {
    return (
      <Alert color="red" data-testid="options-load-error">
        {t('configurator.options.loadError')}
      </Alert>
    )
  }

  if (isLoading || !spec) {
    return (
      <Text size="sm" c="dimmed">
        {t('common.loading')}
      </Text>
    )
  }

  if (spec.options.length === 0) {
    return (
      <Alert color="gray" icon={<Info size={16} />}>
        {t('configurator.options.empty')}
      </Alert>
    )
  }

  const groups = new Map<string | null, CatalogueOptionRead[]>()
  for (const o of spec.options) {
    const key = o.optionGroup
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(o)
  }

  // Selecting a package offers to add its contents — "never silently,
  // never refuses deselection" (FR-C-06). Unchecking a package never
  // triggers the offer, and it also retracts any offer already showing
  // for it (a deselected package has no business asking to add contents
  // it no longer represents).
  const handleToggle = (option: CatalogueOptionRead) => {
    const wasChecked = isChecked(option)
    onToggle(option)
    if (!option.isPackage) return
    if (wasChecked) {
      setPendingPackageIds((prev) => {
        if (!prev.has(option.id)) return prev
        const next = new Set(prev)
        next.delete(option.id)
        return next
      })
      return
    }
    if (missingContentsFor(option).length > 0) {
      setPendingPackageIds((prev) => new Set(prev).add(option.id))
    }
  }

  const respondToOffer = (option: CatalogueOptionRead, missing: CatalogueOptionRead[], accept: boolean) => {
    if (accept) {
      onAddPackageContents(missing)
      // A package added as someone else's content can itself be a package
      // with its own missing contents — offer that too, rather than
      // silently skipping the nested case (accounting for the batch just
      // accepted, since `selected` won't reflect it until the next render).
      const addedIds = new Set(missing.map((o) => o.id))
      setPendingPackageIds((prev) => {
        const next = new Set(prev)
        next.delete(option.id)
        for (const child of missing) {
          if (child.isPackage && missingContentsFor(child, addedIds).length > 0) next.add(child.id)
        }
        return next
      })
    } else {
      setPendingPackageIds((prev) => {
        const next = new Set(prev)
        next.delete(option.id)
        return next
      })
    }
  }

  const relationLines = (option: CatalogueOptionRead): string[] => {
    const lines: string[] = []
    for (const r of outgoing.get(option.id) ?? []) {
      const key = RELATION_I18N_KEY[r.relationType]
      if (!key) continue
      lines.push(
        t(`configurator.options.relations.${key}`, {
          option: r.toOptionDescription,
          price: r.priceInCombination != null ? formatCurrencyChf(Number(r.priceInCombination)) : '',
        }),
      )
    }
    for (const r of incoming.get(option.id) ?? []) {
      const other = optionsById.get(r.fromOptionId)
      if (!other) continue
      const key = r.relationType === 'contains' ? 'containedIn' : RELATION_I18N_KEY[r.relationType]
      if (!key) continue
      lines.push(
        t(`configurator.options.relations.${key}`, {
          option: other.description,
          price: r.priceInCombination != null ? formatCurrencyChf(Number(r.priceInCombination)) : '',
        }),
      )
    }
    return lines
  }

  return (
    <Stack gap="md" data-testid="options-tab">
      {!spec.packagesAvailable && (
        <Alert
          color="yellow"
          icon={<Plug size={16} />}
          title={t('configurator.options.unavailable.title')}
          data-testid="options-entitlement-notice"
        >
          {t('configurator.options.unavailable.body')}
        </Alert>
      )}

      {visibleConflicts.length > 0 && (
        <Alert
          color="yellow"
          icon={<AlertTriangle size={16} />}
          title={t('configurator.options.conflict.title')}
          data-testid="options-conflict-warning"
        >
          <Stack gap={4}>
            <Text size="sm">{t('configurator.options.conflict.body')}</Text>
            {visibleConflicts.map((c) => (
              <Text size="sm" key={c.key}>
                {c.a.description} · {c.b.description}
              </Text>
            ))}
            <Group justify="flex-end">
              <Button
                size="xs"
                variant="subtle"
                onClick={() =>
                  setDismissedConflicts((prev) => new Set([...prev, ...visibleConflicts.map((c) => c.key)]))
                }
              >
                {t('configurator.options.conflict.dismiss')}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {[...groups.entries()].map(([groupCode, groupOptions]) => (
        <Stack key={groupCode ?? '__ungrouped'} gap="xs">
          {groupCode && <Text fw={600} size="sm">{optionGroupLabel(groupCode) ?? groupCode}</Text>}
          {groupOptions.map((option) => {
            const showFeatures = isSelected(option)
            const features = featuresFor(option)
            const lines = relationLines(option)
            const missing = pendingPackageIds.has(option.id) ? missingContentsFor(option) : []
            return (
              <Stack key={option.id} gap={4} data-testid={`option-row-${option.id}`}>
                <Group justify="space-between" wrap="nowrap">
                  {option.isIncluded ? (
                    <Group gap="xs">
                      <Text size="sm">{option.description}</Text>
                      <Badge size="xs" color="gray" variant="light">
                        {t('configurator.options.included')}
                      </Badge>
                    </Group>
                  ) : (
                    <Checkbox
                      label={option.description}
                      checked={isChecked(option)}
                      onChange={() => handleToggle(option)}
                    />
                  )}
                  {mode === 'build' && option.price != null && !option.isIncluded && (
                    <Text size="sm" c="dimmed">
                      {formatCurrencyChf(Number(option.price))}
                    </Text>
                  )}
                </Group>

                {lines.map((line, i) => (
                  <Text key={i} size="xs" c="dimmed" ml="lg">
                    {line}
                  </Text>
                ))}

                {missing.length > 0 && (
                  <Alert color="blue" data-testid={`package-offer-${option.id}`}>
                    <Stack gap="xs">
                      <Text size="sm">
                        {t('configurator.options.packageOffer.body', {
                          package: option.description,
                          contents: missing.map((o) => o.description).join(', '),
                        })}
                      </Text>
                      <Group gap="xs">
                        <Button size="xs" onClick={() => respondToOffer(option, missing, true)}>
                          {t('configurator.options.packageOffer.add')}
                        </Button>
                        <Button size="xs" variant="default" onClick={() => respondToOffer(option, missing, false)}>
                          {t('configurator.options.packageOffer.skip')}
                        </Button>
                      </Group>
                    </Stack>
                  </Alert>
                )}

                {showFeatures && (
                  <Group gap={4} wrap="wrap" ml="lg">
                    {features.map((code) => (
                      <Badge
                        key={code}
                        size="sm"
                        variant="outline"
                        rightSection={
                          <CloseButton
                            size="xs"
                            aria-label={t('common.remove')}
                            onClick={() => onUpdateFeatures(option.id, features.filter((c) => c !== code))}
                          />
                        }
                      >
                        {equipmentFeatureLabel(code)}
                      </Badge>
                    ))}
                    <Select
                      size="xs"
                      placeholder={t('configurator.options.addFeature')}
                      data={equipmentFeatureOptions.filter((f) => !features.includes(f.value))}
                      value={null}
                      onChange={(value) => {
                        if (value) onUpdateFeatures(option.id, [...features, value])
                      }}
                      w={180}
                      searchable
                    />
                  </Group>
                )}
              </Stack>
            )
          })}
        </Stack>
      ))}
    </Stack>
  )
}
