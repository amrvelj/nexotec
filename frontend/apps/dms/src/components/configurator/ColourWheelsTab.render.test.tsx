// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { testTheme } from '../../test/testTheme'
import i18n from '../../i18n'
import { ColourWheelsTab, type ColourWheelsTabProps } from './ColourWheelsTab'
import type { CatalogueSpecificationRead } from '../../api/types'
import { detailsPresent } from '../../test/catalogueDetails'

// KAN-43 (C-E) exit criterion 4: colours and tyre dimensions (with BemDe
// shown) render. FR-C-07: free text is always the primary input; a
// catalogue pick assists it, never replaces it. FR-C-08: the surcharge is
// a price line in build mode only.

function buildSpec(over: Partial<CatalogueSpecificationRead> = {}): CatalogueSpecificationRead {
  return {
    hasCatalogueMatch: true,
    hasProviderConnection: true,
    packagesAvailable: true,
    imagesAvailable: true,
    dealerCanUploadImages: false,
    details: detailsPresent,
    options: [],
    optionRelations: [],
    colours: [
      { colourCode: 'BLK', description: 'Black metallic', colourType: 'exterior', price: '0.00' },
      { colourCode: 'RED', description: 'Rosso competizione', colourType: 'exterior', price: '1100.00' },
      { colourCode: 'GRY', description: 'Grey cloth', colourType: 'interior', price: null },
    ],
    tyreSpecs: [
      { axle: 'front', size: '225/45 R18', loadIndex: '95', speedRating: 'Y', remark: 'nur mit Leichtmetallfelgen', season: 'summer' },
      { axle: 'rear', size: '225/45 R18', loadIndex: '95', speedRating: 'Y', remark: null, season: 'summer' },
    ],
    images: [],
    ...over,
  }
}

// KAN-211 (Anto, 2026-11-08): the type is the advisor's pick from the
// platform lists; the identAPI delivers a name and no finish.
const COLOUR_TYPES = [
  { value: 'solid', label: 'Uni (solid)' },
  { value: 'metallic', label: 'Metallic (metallic)' },
]
const INTERIOR_TYPES = [{ value: 'leather', label: 'Leder (leather)' }]

function defaultProps(props: Partial<ColourWheelsTabProps> = {}): ColourWheelsTabProps {
  return {
    spec: buildSpec(),
    mode: 'build',
    exteriorColour: '',
    interiorColour: '',
    exteriorColourType: '',
    interiorColourType: '',
    exteriorColourTypeOptions: { options: COLOUR_TYPES, isError: false },
    interiorColourTypeOptions: { options: INTERIOR_TYPES, isError: false },
    exteriorColourSurcharge: '',
    interiorColourSurcharge: '',
    wheels: '',
    wheelsSurcharge: '',
    onChange: vi.fn(),
    ...props,
  }
}

function renderTab(props: Partial<ColourWheelsTabProps> = {}) {
  const onChange = vi.fn()
  render(
    <MantineProvider theme={testTheme}>
      <ColourWheelsTab {...defaultProps({ onChange, ...props })} />
    </MantineProvider>,
  )
  return { onChange }
}

describe('ColourWheelsTab', () => {
  it('typing in the free-text colour fields calls onChange directly', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    await user.type(screen.getByLabelText(i18n.t('configurator.colour.exterior')), 'X')
    expect(onChange).toHaveBeenCalledWith({ exteriorColour: 'X' })
  })

  it('picking a catalogue colour fills the text field and the surcharge', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    const pickers = screen.getAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))
    await user.click(pickers[0])
    await user.click(await screen.findByRole('option', { name: 'Rosso competizione' }))
    expect(onChange).toHaveBeenCalledWith({ exteriorColour: 'Rosso competizione', exteriorColourSurcharge: '1100.00' })
  })

  it('a colour with no catalogue price clears the surcharge rather than leaving a stale value', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    const pickers = screen.getAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))
    await user.click(pickers[1])
    await user.click(await screen.findByRole('option', { name: 'Grey cloth' }))
    expect(onChange).toHaveBeenCalledWith({ interiorColour: 'Grey cloth', interiorColourSurcharge: '' })
  })

  it('picking a catalogue colour in record mode fills the text field but never writes a surcharge', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab({ mode: 'record' })
    const pickers = screen.getAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))
    await user.click(pickers[0])
    await user.click(await screen.findByRole('option', { name: 'Rosso competizione' }))
    expect(onChange).toHaveBeenCalledWith({ exteriorColour: 'Rosso competizione' })
    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ exteriorColourSurcharge: expect.anything() }))
  })

  it('shows no catalogue picker for a colour type the catalogue has nothing for', () => {
    renderTab({ spec: buildSpec({ colours: [] }) })
    expect(screen.queryByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))).not.toBeInTheDocument()
  })

  it('hides the surcharge inputs in record mode', () => {
    renderTab({ mode: 'record' })
    expect(screen.queryByLabelText(i18n.t('configurator.colour.surcharge'))).not.toBeInTheDocument()
  })

  it('shows one surcharge input per section in build mode, each wired to its own field', () => {
    renderTab({
      mode: 'build',
      exteriorColourSurcharge: '10.00',
      interiorColourSurcharge: '20.00',
      wheelsSurcharge: '30.00',
    })
    const surchargeInputs = screen.getAllByLabelText(i18n.t('configurator.colour.surcharge'))
    expect(surchargeInputs).toHaveLength(3)
    expect(surchargeInputs.map((input) => (input as HTMLInputElement).value)).toEqual(['CHF 10.00', 'CHF 20.00', 'CHF 30.00']) // KAN-168
  })

  it('renders the tyre-spec reference table with the BemDe remark shown inline', () => {
    renderTab()
    const table = screen.getByTestId('tyre-spec-table')
    expect(within(table).getAllByText('225/45 R18')).toHaveLength(2)
    expect(within(table).getByText('nur mit Leichtmetallfelgen')).toBeInTheDocument()
    expect(within(table).getByText(i18n.t('configurator.wheels.axlePosition.front'))).toBeInTheDocument()
    expect(within(table).getAllByText(i18n.t('configurator.wheels.seasonLabel.summer'))).toHaveLength(2)
  })

  it('does not render the tyre-spec table when the catalogue has no tyre data', () => {
    renderTab({ spec: buildSpec({ tyreSpecs: [] }) })
    expect(screen.queryByTestId('tyre-spec-table')).not.toBeInTheDocument()
  })

  it('the wheels free-text field always renders, even for a manual draft with no spec', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab({ spec: undefined })
    await user.type(screen.getByLabelText(i18n.t('configurator.wheels.description')), 'X')
    expect(onChange).toHaveBeenCalledWith({ wheels: 'X' })
    expect(screen.queryByTestId('tyre-spec-table')).not.toBeInTheDocument()
  })
  it('offers a colour whose FarbArt is an unresolved gap in neither group, and names an unresolved axle (KAN-75)', async () => {
    const user = userEvent.setup()
    renderTab({
      spec: buildSpec({
        colours: [
          { colourCode: 'BLK', description: 'Black metallic', colourType: 'exterior', price: '0.00' },
          { colourCode: 'X1', description: 'Unklassiert', colourType: null, price: null },
          { colourCode: 'GRY', description: 'Grey cloth', colourType: 'interior', price: null },
        ],
        tyreSpecs: [{ axle: null, size: '255/35 R19', loadIndex: null, speedRating: null, remark: null, season: null }],
      }),
    })
    for (const picker of screen.getAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))) {
      await user.click(picker)
      expect(screen.queryByRole('option', { name: 'Unklassiert' })).not.toBeInTheDocument()
      await user.keyboard('{Escape}')
    }
    const row = screen.getByRole('row', { name: /255\/35 R19/ })
    expect(within(row).getByText(i18n.t('configurator.wheels.axlePosition.unclassified'))).toBeInTheDocument()
  })
})

// KAN-211 (Anto, 2026-11-08): «show the colour name as delivered; we ask the
// user to select manually the colour type, as well as for interior».
describe('ColourWheelsTab — colour type (KAN-211)', () => {
  it('offers the paint type from the colour_type list and the upholstery type from its own list', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    await user.click(screen.getByTestId('exteriorColourType-select'))
    await user.click(await screen.findByRole('option', { name: 'Metallic (metallic)' }))
    expect(onChange).toHaveBeenCalledWith({ exteriorColourType: 'metallic' })

    await user.click(screen.getByTestId('interiorColourType-select'))
    await user.click(await screen.findByRole('option', { name: 'Leder (leather)' }))
    expect(onChange).toHaveBeenCalledWith({ interiorColourType: 'leather' })
  })

  it('picking a catalogue colour fills the name as delivered and leaves the type to the advisor', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    const pickers = screen.getAllByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))
    await user.click(pickers[0])
    await user.click(await screen.findByRole('option', { name: 'Rosso competizione' }))
    expect(onChange).toHaveBeenCalledWith({ exteriorColour: 'Rosso competizione', exteriorColourSurcharge: '1100.00' })
    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ exteriorColourType: expect.anything() }))
  })

  it('keeps a stored type the list no longer offers visible, labelled and disabled, and clearable', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab({
      exteriorColourType: 'chrome',
      exteriorColourTypeOptions: { options: [...COLOUR_TYPES, { value: 'chrome', label: 'Chrom (chrome)', disabled: true }], isError: false },
    })
    const select = screen.getByTestId('exteriorColourType-select') as HTMLInputElement
    expect(select.value).toBe('Chrom (chrome)')
    // Mantine marks the clear button aria-hidden (the input itself clears on
    // Backspace); it is still the one the mouse user clicks.
    await user.click(screen.getByLabelText(i18n.t('common.clear')))
    expect(onChange).toHaveBeenCalledWith({ exteriorColourType: '' })
  })

  it('says so when a type list could not be loaded', () => {
    renderTab({ exteriorColourTypeOptions: { options: [], isError: true } })
    expect(screen.getByText(i18n.t('configurator.colour.typeListUnavailable'))).toBeInTheDocument()
  })

  it('the type selects work for a manual draft with no catalogue specification', () => {
    renderTab({ spec: undefined })
    expect(screen.getByTestId('exteriorColourType-select')).toBeInTheDocument()
    expect(screen.getByTestId('interiorColourType-select')).toBeInTheDocument()
  })

  it('lets the stored code stand in while the list has not arrived, never a marker', () => {
    renderTab({ exteriorColourType: 'metallic', exteriorColourTypeOptions: { options: [], isError: false } })
    expect((screen.getByTestId('exteriorColourType-select') as HTMLInputElement).value).toBe('metallic')
  })
})

// KAN-211 / KAN-212 (ADR-123): why the catalogue has no colour or tyre data.
describe('ColourWheelsTab — per-detail state (KAN-211, KAN-212)', () => {
  it('says the colours are not in the contract and still offers the free-text fields', () => {
    renderTab({
      spec: buildSpec({ colours: [], details: { ...detailsPresent, colours: { state: 'notInContract', loadedAt: '2026-11-08T10:00:00Z' } } }),
    })
    expect(screen.getByTestId('detail-state-colours')).toHaveTextContent(i18n.t('configurator.details.notInContract.title'))
    expect(screen.getByLabelText(i18n.t('configurator.colour.exterior'))).toBeInTheDocument()
    expect(screen.queryByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))).not.toBeInTheDocument()
  })

  it('says the tyre data were not delivered, in the wheels section', () => {
    renderTab({
      spec: buildSpec({ tyreSpecs: [], details: { ...detailsPresent, tyreSpecs: { state: 'unavailable', loadedAt: '2026-11-08T10:00:00Z' } } }),
    })
    expect(screen.getByTestId('detail-state-tyreSpecs')).toHaveTextContent(i18n.t('configurator.details.unavailable.title'))
    expect(screen.queryByTestId('tyre-spec-table')).not.toBeInTheDocument()
  })

  it('renders no state notice when both details are loaded', () => {
    renderTab()
    expect(screen.queryByTestId('detail-state-colours')).not.toBeInTheDocument()
    expect(screen.queryByTestId('detail-state-tyreSpecs')).not.toBeInTheDocument()
  })

  it('says the provider no longer lists the variant when the loaded colours carry that note', () => {
    // `_load_colours` ends a vanished key in `present` with `variant_unknown_at_provider`:
    // no picker, and the reason said — translated, never the raw note.
    renderTab({
      spec: buildSpec({
        colours: [],
        details: { ...detailsPresent, colours: { state: 'present', loadedAt: '2026-11-08T10:00:00Z', note: 'variant_unknown_at_provider' } },
      }),
    })
    const notice = screen.getByTestId('detail-state-colours')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.notes.variantUnknownAtProvider'))
    expect(notice).not.toHaveTextContent('variant_unknown_at_provider')
    expect(screen.queryByLabelText(i18n.t('configurator.colour.pickFromCatalogue'))).not.toBeInTheDocument()
  })
})

// KAN-598 (Anto, 2026-11-11, option A): a refused request on colours or tyres is said.
describe('ColourWheelsTab — a refused request (KAN-598)', () => {
  it('says auto-i-dat refused the colour request', () => {
    renderTab({ spec: buildSpec({ colours: [], details: { ...detailsPresent, colours: { state: 'present', loadedAt: '2026-11-08T10:00:00Z', note: 'ProviderRequestError' } } }) })
    const notice = screen.getByTestId('detail-state-colours')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.notes.providerRequestRefused'))
    expect(notice).not.toHaveTextContent('ProviderRequestError')
  })

  it('says auto-i-dat refused the tyre request', () => {
    renderTab({ spec: buildSpec({ tyreSpecs: [], details: { ...detailsPresent, tyreSpecs: { state: 'present', loadedAt: '2026-11-08T10:00:00Z', note: 'ProviderRequestError' } } }) })
    const notice = screen.getByTestId('detail-state-tyreSpecs')
    expect(notice).toHaveTextContent(i18n.t('configurator.details.notes.providerRequestRefused'))
    expect(notice).not.toHaveTextContent('ProviderRequestError')
  })
})
