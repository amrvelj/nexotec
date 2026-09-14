// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../../i18n'
import { ImagesTab, type ImagesTabProps } from './ImagesTab'
import type { CatalogueSpecificationRead } from '../../api/types'

// KAN-43 (C-E) / FR-C-09: small(210px)/large(1200px), exterior/interior,
// entitlement-gated. No fabricated "upload" affordance — no upload
// endpoint exists anywhere in the backend yet.

function buildSpec(over: Partial<CatalogueSpecificationRead> = {}): CatalogueSpecificationRead {
  return {
    hasCatalogueMatch: true,
    hasProviderConnection: true,
    packagesAvailable: true,
    imagesAvailable: true,
    dealerCanUploadImages: false,
    options: [],
    optionRelations: [],
    colours: [],
    tyreSpecs: [],
    images: [
      { imageKey: 'fz1-front.jpg', bildTyp: 'S', bildArt: 'A', sequence: 1, imageUrl: 'https://images.autoi.ch/img/fz1-front.jpg' },
      { imageKey: 'fz1-interior.jpg', bildTyp: 'L', bildArt: 'I', sequence: 0, imageUrl: null },
    ],
    ...over,
  }
}

function renderTab(props: Partial<ImagesTabProps> = {}) {
  render(
    <MantineProvider theme={theme}>
      <ImagesTab spec={buildSpec()} isLoading={false} isManual={false} {...props} />
    </MantineProvider>,
  )
}

describe('ImagesTab', () => {
  it('renders each image with its type/area badges, ordered by sequence', () => {
    renderTab()
    const grid = screen.getByTestId('images-tab')
    const cards = within(grid).getAllByTestId(/^image-/)
    expect(cards).toHaveLength(2)
    // sequence 0 (interior) comes before sequence 1 (front/exterior)
    expect(cards[0].dataset.testid).toBe('image-fz1-interior.jpg')
    expect(within(cards[0]).getByText(i18n.t('configurator.images.bildArt.interior'))).toBeInTheDocument()
    expect(within(cards[0]).getByText(i18n.t('configurator.images.bildTyp.large'))).toBeInTheDocument()
    expect(within(cards[1]).getByText(i18n.t('configurator.images.bildArt.exterior'))).toBeInTheDocument()
    expect(within(cards[1]).getByText(i18n.t('configurator.images.bildTyp.small'))).toBeInTheDocument()
  })

  it('renders a real <img> when imageUrl is present', () => {
    renderTab()
    const card = screen.getByTestId('image-fz1-front.jpg')
    const img = within(card).getByRole('img')
    expect(img).toHaveAttribute('src', 'https://images.autoi.ch/img/fz1-front.jpg')
  })

  it('falls back to a text reference when imageUrl is missing (a pre-fix synced row)', () => {
    renderTab()
    const card = screen.getByTestId('image-fz1-interior.jpg')
    expect(within(card).queryByRole('img')).not.toBeInTheDocument()
    expect(within(card).getByText('fz1-interior.jpg')).toBeInTheDocument()
  })

  it('shows the entitlement notice, never a fake upload button, when images are unavailable', () => {
    renderTab({ spec: buildSpec({ imagesAvailable: false, images: [], dealerCanUploadImages: true }) })
    expect(screen.getByTestId('images-entitlement-notice')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByTestId('images-tab')).not.toBeInTheDocument()
  })

  it('shows the upload note only when the dealer is entitled to add their own photos', () => {
    renderTab({ spec: buildSpec({ imagesAvailable: false, images: [], dealerCanUploadImages: true }) })
    expect(screen.getByText(i18n.t('configurator.images.unavailable.uploadNote'))).toBeInTheDocument()
  })

  it('omits the upload note when the dealer cannot add their own photos', () => {
    renderTab({ spec: buildSpec({ imagesAvailable: false, images: [], dealerCanUploadImages: false }) })
    expect(screen.queryByText(i18n.t('configurator.images.unavailable.uploadNote'))).not.toBeInTheDocument()
  })

  it('never renders an empty badge for an image with no bildArt/bildTyp code', () => {
    renderTab({
      spec: buildSpec({
        images: [{ imageKey: 'fz1-unknown.jpg', bildTyp: '', bildArt: '', sequence: 0, imageUrl: null }],
      }),
    })
    const card = screen.getByTestId('image-fz1-unknown.jpg')
    expect(card.querySelectorAll('[class*="mantine-Badge-root"]')).toHaveLength(0)
  })

  it('shows an empty-state message when the variant has no images', () => {
    renderTab({ spec: buildSpec({ images: [] }) })
    expect(screen.getByText(i18n.t('configurator.images.empty'))).toBeInTheDocument()
  })

  it('shows the no-catalogue-variant message for a manual configuration', () => {
    renderTab({ isManual: true, spec: undefined })
    expect(screen.getByTestId('images-no-catalogue-variant')).toBeInTheDocument()
  })

  it('shows a loading state while the specification query is in flight', () => {
    renderTab({ isLoading: true, spec: undefined })
    expect(screen.getByText(i18n.t('common.loading'))).toBeInTheDocument()
  })
})
