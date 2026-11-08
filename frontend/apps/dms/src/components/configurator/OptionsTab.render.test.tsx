// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { testTheme } from '../../test/testTheme'
import i18n from '../../i18n'
import { OptionsTab, type OptionsTabProps } from './OptionsTab'
import type {
  CatalogueOptionRead,
  CatalogueOptionRelationRead,
  CatalogueSpecificationRead,
  ConfigurationOptionInput,
} from '../../api/types'
import { detailsPresent } from '../../test/catalogueDetails'
import { optionMatchesSearch } from './optionSearch'

// KAN-43 (C-E) exit criteria 1-3, 5: grouped options with relations
// inline, a conflicting selection warns and proceeds (never blocks), a
// package offers its contents, and a missing entitlement explains itself
// on screen rather than rendering an empty panel.

const PACK: CatalogueOptionRead = {
  id: 'opt-pack',
  optionCode: 'WNTR',
  description: 'Winter package',
  optionGroup: 'comfort',
  price: '450.00',
  isIncluded: false,
  isPackage: true,
  equipmentFeatures: [],
}
const CHILD: CatalogueOptionRead = {
  id: 'opt-child',
  optionCode: 'HEAT',
  description: 'Heated seats',
  optionGroup: 'comfort',
  price: '300.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}
const INCLUDED: CatalogueOptionRead = {
  id: 'opt-ac',
  optionCode: 'AC',
  description: 'Air conditioning',
  optionGroup: 'comfort',
  price: '0.00',
  isIncluded: true,
  isPackage: false,
  equipmentFeatures: ['air_conditioning'],
}
const SPORT: CatalogueOptionRead = {
  id: 'opt-sport',
  optionCode: 'SPORT',
  description: 'Sport seats',
  optionGroup: 'interior',
  price: '900.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}
const FOG: CatalogueOptionRead = {
  id: 'opt-fog',
  optionCode: 'FOG',
  description: 'Fog lights',
  optionGroup: 'exterior',
  price: '200.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}

// A second, independent package/child pair (finding: two simultaneous
// package offers must not clobber each other).
const PACK2: CatalogueOptionRead = {
  id: 'opt-pack2',
  optionCode: 'CITY',
  description: 'City package',
  optionGroup: 'comfort',
  price: '350.00',
  isIncluded: false,
  isPackage: true,
  equipmentFeatures: [],
}
const CHILD2: CatalogueOptionRead = {
  id: 'opt-child2',
  optionCode: 'PARK',
  description: 'Park assist',
  optionGroup: 'comfort',
  price: '400.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}

// A nested package chain (finding: accepting an outer package's offer must
// still offer an inner package's own missing contents).
const OUTER: CatalogueOptionRead = {
  id: 'opt-outer',
  optionCode: 'OUTER',
  description: 'Outer package',
  optionGroup: 'comfort',
  price: '1000.00',
  isIncluded: false,
  isPackage: true,
  equipmentFeatures: [],
}
const MID: CatalogueOptionRead = {
  id: 'opt-mid',
  optionCode: 'MID',
  description: 'Mid package',
  optionGroup: 'comfort',
  price: '500.00',
  isIncluded: false,
  isPackage: true,
  equipmentFeatures: [],
}
const DEEP: CatalogueOptionRead = {
  id: 'opt-deep',
  optionCode: 'DEEP',
  description: 'Deep option',
  optionGroup: 'comfort',
  price: '100.00',
  isIncluded: false,
  isPackage: false,
  equipmentFeatures: [],
}

const RELATIONS: CatalogueOptionRelationRead[] = [
  {
    fromOptionId: 'opt-pack',
    toOptionId: 'opt-child',
    toOptionCode: 'HEAT',
    toOptionDescription: 'Heated seats',
    relationType: 'contains',
    priceInCombination: null,
  },
  {
    fromOptionId: 'opt-sport',
    toOptionId: 'opt-fog',
    toOptionCode: 'FOG',
    toOptionDescription: 'Fog lights',
    relationType: 'excludes',
    priceInCombination: null,
  },
]

const GROUP_LABELS: Record<string, string> = { comfort: 'Comfort', interior: 'Interior', exterior: 'Exterior' }
const FEATURE_LABELS: Record<string, string> = { air_conditioning: 'Air conditioning', navigation: 'Navigation' }

function buildSpec(over: Partial<CatalogueSpecificationRead> = {}): CatalogueSpecificationRead {
  return {
    hasCatalogueMatch: true,
    hasProviderConnection: true,
    packagesAvailable: true,
    imagesAvailable: true,
    dealerCanUploadImages: false,
    details: detailsPresent,
    options: [PACK, CHILD, INCLUDED, SPORT, FOG],
    colours: [],
    tyreSpecs: [],
    images: [],
    optionRelations: RELATIONS,
    ...over,
  }
}

function toInput(option: CatalogueOptionRead): ConfigurationOptionInput {
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

function defaultProps(props: Partial<OptionsTabProps> = {}): OptionsTabProps {
  return {
    spec: buildSpec(),
    isLoading: false,
    isError: false,
    isManual: false,
    mode: 'build',
    selected: new Map(),
    onToggle: vi.fn(),
    onAddPackageContents: vi.fn(),
    onUpdateFeatures: vi.fn(),
    optionGroupLabel: (code) => GROUP_LABELS[code] ?? code,
    equipmentFeatureLabel: (code) => FEATURE_LABELS[code] ?? code,
    equipmentFeatureOptions: [{ value: 'navigation', label: 'Navigation' }],
    ...props,
  }
}

function renderTab(props: Partial<OptionsTabProps> = {}) {
  const resolved = defaultProps(props)
  const utils = render(
    <MantineProvider theme={testTheme}>
      <OptionsTab {...resolved} />
    </MantineProvider>,
  )
  const rerenderWith = (over: Partial<OptionsTabProps>) => {
    const next = { ...resolved, ...over }
    utils.rerender(
      <MantineProvider theme={testTheme}>
        <OptionsTab {...next} />
      </MantineProvider>,
    )
  }
  return {
    onToggle: resolved.onToggle as ReturnType<typeof vi.fn>,
    onAddPackageContents: resolved.onAddPackageContents as ReturnType<typeof vi.fn>,
    onUpdateFeatures: resolved.onUpdateFeatures as ReturnType<typeof vi.fn>,
    rerenderWith,
  }
}

describe('OptionsTab', () => {
  it('groups options by optionGroup with resolved labels', () => {
    renderTab()
    expect(screen.getByText('Comfort')).toBeInTheDocument()
    expect(screen.getByText('Interior')).toBeInTheDocument()
    expect(screen.getByText('Exterior')).toBeInTheDocument()
    expect(screen.getByText('Winter package')).toBeInTheDocument()
    expect(screen.getByText('Sport seats')).toBeInTheDocument()
  })

  it('renders a relation inline on the source row and its reverse on the target row', () => {
    renderTab()
    const packRow = screen.getByTestId('option-row-opt-pack')
    expect(within(packRow).getByText(i18n.t('configurator.options.relations.contains', { option: 'Heated seats' }))).toBeInTheDocument()

    const childRow = screen.getByTestId('option-row-opt-child')
    expect(
      within(childRow).getByText(i18n.t('configurator.options.relations.containedIn', { option: 'Winter package' })),
    ).toBeInTheDocument()

    const sportRow = screen.getByTestId('option-row-opt-sport')
    expect(within(sportRow).getByText(i18n.t('configurator.options.relations.excludes', { option: 'Fog lights' }))).toBeInTheDocument()
    const fogRow = screen.getByTestId('option-row-opt-fog')
    expect(within(fogRow).getByText(i18n.t('configurator.options.relations.excludes', { option: 'Sport seats' }))).toBeInTheDocument()
  })

  it('checking a plain option calls onToggle with the catalogue row', async () => {
    const user = userEvent.setup()
    const { onToggle } = renderTab()
    const sportRow = screen.getByTestId('option-row-opt-sport')
    await user.click(within(sportRow).getByRole('checkbox'))
    expect(onToggle).toHaveBeenCalledWith(SPORT)
  })

  it('checking a package with unselected contents offers to add them, never silently', async () => {
    const user = userEvent.setup()
    const { onAddPackageContents } = renderTab()
    const packRow = screen.getByTestId('option-row-opt-pack')
    await user.click(within(packRow).getByRole('checkbox'))

    const offer = await screen.findByTestId('package-offer-opt-pack')
    expect(within(offer).getByText(/Heated seats/)).toBeInTheDocument()

    await user.click(within(offer).getByRole('button', { name: i18n.t('configurator.options.packageOffer.add') }))
    expect(onAddPackageContents).toHaveBeenCalledWith([CHILD])
  })

  it('skipping the package offer never adds the contents', async () => {
    const user = userEvent.setup()
    const { onAddPackageContents } = renderTab()
    const packRow = screen.getByTestId('option-row-opt-pack')
    await user.click(within(packRow).getByRole('checkbox'))
    const offer = await screen.findByTestId('package-offer-opt-pack')

    await user.click(within(offer).getByRole('button', { name: i18n.t('configurator.options.packageOffer.skip') }))
    expect(onAddPackageContents).not.toHaveBeenCalled()
    expect(screen.queryByTestId('package-offer-opt-pack')).not.toBeInTheDocument()
  })

  it('unchecking a package retracts its own pending offer, so accepting it afterwards is impossible', async () => {
    const user = userEvent.setup()
    const { onAddPackageContents, rerenderWith } = renderTab()
    const packRow = screen.getByTestId('option-row-opt-pack')

    await user.click(within(packRow).getByRole('checkbox'))
    await screen.findByTestId('package-offer-opt-pack')

    // The advisor's own click doesn't mutate `selected` (it's a prop,
    // owned by the parent in real life) — simulate the parent committing
    // the check before the advisor unchecks it again.
    rerenderWith({ selected: new Map([[PACK.id, toInput(PACK)]]) })
    const checkedPackRow = screen.getByTestId('option-row-opt-pack')
    await user.click(within(checkedPackRow).getByRole('checkbox'))

    expect(screen.queryByTestId('package-offer-opt-pack')).not.toBeInTheDocument()
    expect(onAddPackageContents).not.toHaveBeenCalled()
  })

  it('two packages checked in sequence each keep their own pending offer', async () => {
    const user = userEvent.setup()
    const spec = buildSpec({
      options: [PACK, CHILD, PACK2, CHILD2, INCLUDED, SPORT, FOG],
      optionRelations: [
        ...RELATIONS,
        {
          fromOptionId: 'opt-pack2',
          toOptionId: 'opt-child2',
          toOptionCode: 'PARK',
          toOptionDescription: 'Park assist',
          relationType: 'contains',
          priceInCombination: null,
        },
      ],
    })
    const { rerenderWith } = renderTab({ spec })

    await user.click(within(screen.getByTestId('option-row-opt-pack')).getByRole('checkbox'))
    await screen.findByTestId('package-offer-opt-pack')

    rerenderWith({ spec, selected: new Map([[PACK.id, toInput(PACK)]]) })
    await user.click(within(screen.getByTestId('option-row-opt-pack2')).getByRole('checkbox'))

    // Both offers must still be showing — checking the second package
    // must not silently discard the first package's still-unanswered one.
    expect(screen.getByTestId('package-offer-opt-pack')).toBeInTheDocument()
    expect(await screen.findByTestId('package-offer-opt-pack2')).toBeInTheDocument()
  })

  it('accepting an outer package offer also offers a nested package’s own missing contents', async () => {
    const user = userEvent.setup()
    const spec = buildSpec({
      options: [OUTER, MID, DEEP, INCLUDED, SPORT, FOG],
      optionRelations: [
        {
          fromOptionId: 'opt-outer',
          toOptionId: 'opt-mid',
          toOptionCode: 'MID',
          toOptionDescription: 'Mid package',
          relationType: 'contains',
          priceInCombination: null,
        },
        {
          fromOptionId: 'opt-mid',
          toOptionId: 'opt-deep',
          toOptionCode: 'DEEP',
          toOptionDescription: 'Deep option',
          relationType: 'contains',
          priceInCombination: null,
        },
      ],
    })
    const { onAddPackageContents } = renderTab({ spec })

    await user.click(within(screen.getByTestId('option-row-opt-outer')).getByRole('checkbox'))
    const outerOffer = await screen.findByTestId('package-offer-opt-outer')
    expect(within(outerOffer).getByText(/Mid package/)).toBeInTheDocument()

    await user.click(within(outerOffer).getByRole('button', { name: i18n.t('configurator.options.packageOffer.add') }))
    expect(onAddPackageContents).toHaveBeenCalledWith([MID])

    const midOffer = await screen.findByTestId('package-offer-opt-mid')
    expect(within(midOffer).getByText(/Deep option/)).toBeInTheDocument()
  })

  it('does not offer a package whose contents are already selected', async () => {
    const user = userEvent.setup()
    const selected = new Map([[CHILD.id, toInput(CHILD)]])
    renderTab({ selected })
    const packRow = screen.getByTestId('option-row-opt-pack')
    await user.click(within(packRow).getByRole('checkbox'))
    expect(screen.queryByTestId('package-offer-opt-pack')).not.toBeInTheDocument()
  })

  it('ADR-072 — a conflicting selection warns but is never blocked, and the warning is dismissible', async () => {
    const user = userEvent.setup()
    const selected = new Map([
      [SPORT.id, toInput(SPORT)],
      [FOG.id, toInput(FOG)],
    ])
    renderTab({ selected })

    const warning = await screen.findByTestId('options-conflict-warning')
    expect(within(warning).getByText(/Sport seats/)).toBeInTheDocument()
    expect(within(warning).getByText(/Fog lights/)).toBeInTheDocument()

    // Both checkboxes stay checked — the warning never unchecks anything.
    const sportRow = screen.getByTestId('option-row-opt-sport')
    const fogRow = screen.getByTestId('option-row-opt-fog')
    expect(within(sportRow).getByRole('checkbox')).toBeChecked()
    expect(within(fogRow).getByRole('checkbox')).toBeChecked()

    await user.click(within(warning).getByRole('button', { name: i18n.t('configurator.options.conflict.dismiss') }))
    expect(screen.queryByTestId('options-conflict-warning')).not.toBeInTheDocument()
  })

  it('a dismissed conflict reappears once it disappears and is recreated (it is a fresh occurrence, not the same one)', async () => {
    const user = userEvent.setup()
    const { rerenderWith } = renderTab({
      selected: new Map([
        [SPORT.id, toInput(SPORT)],
        [FOG.id, toInput(FOG)],
      ]),
    })
    const warning = await screen.findByTestId('options-conflict-warning')
    await user.click(within(warning).getByRole('button', { name: i18n.t('configurator.options.conflict.dismiss') }))
    expect(screen.queryByTestId('options-conflict-warning')).not.toBeInTheDocument()

    // Unchecking Fog lights genuinely resolves the conflict — still hidden.
    rerenderWith({ selected: new Map([[SPORT.id, toInput(SPORT)]]) })
    expect(screen.queryByTestId('options-conflict-warning')).not.toBeInTheDocument()

    // Rechecking Fog lights recreates the identical Sport/Fog pair — this
    // is a fresh occurrence and must be shown again, not silently
    // suppressed by the earlier dismissal of the same pair.
    rerenderWith({
      selected: new Map([
        [SPORT.id, toInput(SPORT)],
        [FOG.id, toInput(FOG)],
      ]),
    })
    expect(screen.getByTestId('options-conflict-warning')).toBeInTheDocument()
  })

  it('dismissing two independent conflicts together keeps the untouched one hidden after the other resolves', async () => {
    const user = userEvent.setup()
    // A second, independent excludes pair sharing no options with Sport/Fog.
    const spec = buildSpec({
      optionRelations: [
        ...RELATIONS,
        {
          fromOptionId: 'opt-pack',
          toOptionId: 'opt-ac',
          toOptionCode: 'AC',
          toOptionDescription: 'Air conditioning',
          relationType: 'excludes',
          priceInCombination: null,
        },
      ],
    })
    const { rerenderWith } = renderTab({
      spec,
      selected: new Map([
        [SPORT.id, toInput(SPORT)],
        [FOG.id, toInput(FOG)],
        [PACK.id, toInput(PACK)],
      ]),
    })
    const warning = await screen.findByTestId('options-conflict-warning')
    await user.click(within(warning).getByRole('button', { name: i18n.t('configurator.options.conflict.dismiss') }))
    expect(screen.queryByTestId('options-conflict-warning')).not.toBeInTheDocument()

    // Resolve only the Sport/Fog pair — the independent Pack/AC pair is
    // still selected and still conflicting, and must stay dismissed.
    rerenderWith({
      spec,
      selected: new Map([
        [FOG.id, toInput(FOG)],
        [PACK.id, toInput(PACK)],
      ]),
    })
    expect(screen.queryByTestId('options-conflict-warning')).not.toBeInTheDocument()
  })

  it('an included option shows an Included badge, no checkbox, and no price even in build mode', () => {
    renderTab()
    const row = screen.getByTestId('option-row-opt-ac')
    // "Air conditioning" appears twice: the row description and the
    // equipment-feature chip below it.
    expect(within(row).getAllByText('Air conditioning')).toHaveLength(2)
    expect(within(row).getByText(i18n.t('configurator.options.included'))).toBeInTheDocument()
    expect(within(row).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(row).queryByText(/CHF/)).not.toBeInTheDocument()
  })

  it('equipment-feature chips render for an included option and can be removed', async () => {
    const user = userEvent.setup()
    const { onUpdateFeatures } = renderTab()
    const row = screen.getByTestId('option-row-opt-ac')
    expect(within(row).getAllByText('Air conditioning')).toHaveLength(2)

    await user.click(within(row).getByRole('button', { name: i18n.t('common.remove') }))
    expect(onUpdateFeatures).toHaveBeenCalledWith('opt-ac', [])
  })

  it('adding a feature via the picker calls onUpdateFeatures with the new code appended', async () => {
    const user = userEvent.setup()
    const selected = new Map([[SPORT.id, toInput(SPORT)]])
    const { onUpdateFeatures } = renderTab({ selected })
    const row = screen.getByTestId('option-row-opt-sport')
    const picker = within(row).getByPlaceholderText(i18n.t('configurator.options.addFeature'))
    await user.click(picker)
    await user.click(await screen.findByRole('option', { name: 'Navigation' }))
    expect(onUpdateFeatures).toHaveBeenCalledWith('opt-sport', ['navigation'])
  })

  it('without the packages entitlement, options render as a flat list with the entitlement notice and no relations', () => {
    const flatSpec = buildSpec({
      packagesAvailable: false,
      optionRelations: [],
      options: [PACK, CHILD, INCLUDED, SPORT, FOG].map((o) => ({ ...o, optionGroup: null })),
    })
    renderTab({ spec: flatSpec })
    expect(screen.getByTestId('options-entitlement-notice')).toBeInTheDocument()
    expect(screen.queryByText('Comfort')).not.toBeInTheDocument()
    expect(screen.queryByText(/Excludes|Contains|Contained in/)).not.toBeInTheDocument()
    expect(screen.getByText('Winter package')).toBeInTheDocument()
  })

  it('a manual (off-catalogue) configuration shows only the no-catalogue-variant message', () => {
    renderTab({ isManual: true })
    expect(screen.getByTestId('options-no-catalogue-variant')).toBeInTheDocument()
    expect(screen.queryByText('Winter package')).not.toBeInTheDocument()
  })

  it('shows a loading state while the specification query is in flight', () => {
    renderTab({ isLoading: true, spec: undefined })
    expect(screen.getByText(i18n.t('common.loading'))).toBeInTheDocument()
  })

  it('shows an error message instead of a stuck spinner when the specification query fails', () => {
    renderTab({ isError: true, spec: undefined })
    expect(screen.getByTestId('options-load-error')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('common.loading'))).not.toBeInTheDocument()
  })

  it('shows an empty-state message when the variant has no options', () => {
    renderTab({ spec: buildSpec({ options: [] }) })
    expect(screen.getByText(i18n.t('configurator.options.empty'))).toBeInTheDocument()
  })
})

// KAN-211 / KAN-212 (ADR-123): the detail's own load state on screen —
// a licence gap or a failed request is said, never shown as «no options».
describe('OptionsTab — per-detail state (KAN-211, KAN-212)', () => {
  it('says «Nicht in Ihrem Vertrag» instead of the empty state when the licence refused the options', () => {
    renderTab({
      spec: buildSpec({ options: [], details: { ...detailsPresent, options: { state: 'notInContract', loadedAt: '2026-11-08T10:00:00Z' } } }),
    })
    const notice = screen.getByTestId('detail-state-options')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.notInContract.title'))
    expect(screen.queryByText(i18n.t('configurator.options.empty'))).not.toBeInTheDocument()
  })

  it('says the provider did not answer, with the note for support, and that the next open retries', () => {
    renderTab({
      spec: buildSpec({
        options: [],
        details: { ...detailsPresent, options: { state: 'unavailable', loadedAt: '2026-11-08T10:00:00Z', note: 'ProviderGatewayError' } },
      }),
    })
    const notice = screen.getByTestId('detail-state-options')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.unavailable.title'))
    expect(notice).toHaveTextContent(i18n.t('configurator.details.unavailable.body'))
    // A failure's class name is for the call log, never the screen (i18n rule).
    expect(notice).not.toHaveTextContent('ProviderGatewayError')
  })

  it('translates a known note, and says so when the provider no longer lists the variant', () => {
    renderTab({
      spec: buildSpec({
        options: [],
        details: { ...detailsPresent, options: { state: 'present', loadedAt: '2026-11-08T10:00:00Z', note: 'variant_unknown_at_provider' } },
      }),
    })
    expect(screen.getByTestId('detail-state-options')).toHaveTextContent(i18n.t('configurator.details.notes.variantUnknownAtProvider'))
    expect(screen.queryByText(i18n.t('configurator.options.empty'))).not.toBeInTheDocument()
    expect(screen.queryByText('variant_unknown_at_provider')).not.toBeInTheDocument()
  })

  it('says «noch nicht geladen» for a Betrieb that never asked', () => {
    renderTab({ spec: buildSpec({ options: [], details: { ...detailsPresent, options: { state: 'notLoaded' } } }) })
    expect(screen.getByTestId('detail-state-options')).toHaveTextContent(i18n.t('configurator.details.notLoaded.title'))
  })

  it('keeps the stale list visible under the notice when a reload failed', () => {
    renderTab({
      spec: buildSpec({ details: { ...detailsPresent, options: { state: 'unavailable', loadedAt: '2026-11-08T10:00:00Z' } } }),
    })
    expect(screen.getByTestId('detail-state-options')).toBeInTheDocument()
    expect(screen.getByText('Winter package')).toBeInTheDocument()
  })

  it('renders nothing about the state when the options are loaded', () => {
    renderTab()
    expect(screen.queryByTestId('detail-state-options')).not.toBeInTheDocument()
  })

  it('follows the UI language', async () => {
    await i18n.changeLanguage('fr')
    try {
      renderTab({ spec: buildSpec({ options: [], details: { ...detailsPresent, options: { state: 'notInContract', loadedAt: '2026-11-08T10:00:00Z' } } }) })
      expect(screen.getByTestId('detail-state-options')).toHaveTextContent('Pas dans votre contrat')
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})

// KAN-211 (Anto, 2026-11-08): «I want to see all options and I want to be
// able to search by them and their codes».
describe('OptionsTab — search by text and code (KAN-211)', () => {
  const CODED = { ...FOG, manufacturerCode: 'PXA' }

  it('shows the manufacturer code beside the option', () => {
    renderTab({ spec: buildSpec({ options: [CODED, SPORT] }) })
    const row = screen.getByTestId(`option-row-${CODED.id}`)
    expect(within(row).getByText('PXA')).toBeInTheDocument()
  })

  it('narrows the list to options whose text matches, hiding emptied groups', async () => {
    const user = userEvent.setup()
    renderTab({ spec: buildSpec({ options: [CODED, SPORT, INCLUDED] }) })
    await user.type(screen.getByTestId('options-search'), 'sport')
    expect(screen.getByText('Sport seats')).toBeInTheDocument()
    expect(screen.queryByText('Fog lights')).not.toBeInTheDocument()
    expect(screen.queryByText('Air conditioning')).not.toBeInTheDocument()
    expect(screen.queryByText('Exterior')).not.toBeInTheDocument() // the group with no match
  })

  it('finds an option by its manufacturer code, case-insensitively', async () => {
    const user = userEvent.setup()
    renderTab({ spec: buildSpec({ options: [CODED, SPORT] }) })
    await user.type(screen.getByTestId('options-search'), 'pxa')
    expect(screen.getByText('Fog lights')).toBeInTheDocument()
    expect(screen.queryByText('Sport seats')).not.toBeInTheDocument()
  })

  it('says so when nothing matches, and the list comes back when the search is cleared', async () => {
    const user = userEvent.setup()
    renderTab({ spec: buildSpec({ options: [CODED, SPORT] }) })
    const search = screen.getByTestId('options-search')
    await user.type(search, 'zzz')
    expect(screen.getByTestId('options-no-matches')).toBeInTheDocument()
    await user.clear(search)
    expect(screen.queryByTestId('options-no-matches')).not.toBeInTheDocument()
    expect(screen.getByText('Sport seats')).toBeInTheDocument()
  })

  it('matches text, manufacturer code or auto-i key; a blank search matches everything', () => {
    expect(optionMatchesSearch(CODED, '')).toBe(true)
    expect(optionMatchesSearch(CODED, '  ')).toBe(true)
    expect(optionMatchesSearch(CODED, 'FOG')).toBe(true) // optionCode
    expect(optionMatchesSearch(CODED, 'lights')).toBe(true)
    expect(optionMatchesSearch(CODED, 'pxa')).toBe(true)
    expect(optionMatchesSearch(CODED, 'sport')).toBe(false)
    expect(optionMatchesSearch({ ...CODED, manufacturerCode: null }, 'pxa')).toBe(false)
  })
})
