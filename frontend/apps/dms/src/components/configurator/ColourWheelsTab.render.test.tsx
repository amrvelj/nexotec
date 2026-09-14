// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { ColourWheelsTab, type ColourWheelsTabProps } from './ColourWheelsTab'
import type { CatalogueSpecificationRead } from '../../api/types'

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

function defaultProps(props: Partial<ColourWheelsTabProps> = {}): ColourWheelsTabProps {
  return {
    spec: buildSpec(),
    mode: 'build',
    exteriorColour: '',
    interiorColour: '',
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
    <MantineProvider theme={theme}>
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
    expect(surchargeInputs.map((input) => (input as HTMLInputElement).value)).toEqual(['10.00', '20.00', '30.00'])
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
})
