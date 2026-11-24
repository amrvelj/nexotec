// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend } from '../../../../test/fakeBackend'
import { contact } from '../../../../test/fixtures'
import type { ContactRead } from '../../../../api/types'
import { ContactDetailPage } from '../../pages/ContactDetailPage'

// KAN-50 — FR-18 regions 4 (Commercial standing) and 5 (Relationship)
// render on the 360, in FR-18's order, and every new stored field saves
// through PATCH /contacts/{id}.

function routes(overrides: Partial<ContactRead> = {}) {
  return [
    { match: /^\/contacts\/c1\/phones$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/emails$/, handler: () => ({ items: [] }) },
    { match: /^\/contacts\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/contacts\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/offers$/, handler: () => ({ items: [], nextCursor: null }) },
    { match: /^\/sales\/contracts$/, handler: () => ({ items: [], nextCursor: null }) },
    {
      match: /^\/contacts\/advisor-options$/,
      handler: () => ({ items: [{ id: 'user-7', label: 'Rey Ortiz' }, { id: 'user-8', label: 'Bea Adams' }] }),
    },
    { match: /^\/contacts\/c1$/, handler: () => contact({ id: 'c1', ...overrides }) },
  ]
}

function render() {
  renderWithProviders(
    <Routes>
      <Route path="/contacts/:id" element={<ContactDetailPage />} />
    </Routes>,
    { route: '/contacts/c1' },
  )
}

const cardTitle = (key: string) => i18n.t(`contactDetail.overview.cards.${key}`)

const card = (key: string): HTMLElement => {
  const title = screen.getAllByText(cardTitle(key)).find((el) => el.tagName === 'DIV')!
  return title.parentElement!.parentElement as HTMLElement
}

async function ready() {
  await screen.findAllByText(cardTitle('relationship'))
}

const fieldRow = (containerKey: string, fieldKey: string): HTMLElement =>
  within(card(containerKey)).getByText(i18n.t(`contactDetail.overview.fields.${fieldKey}`)).parentElement as HTMLElement

describe('contact 360 — Commercial standing & Relationship (KAN-50)', () => {
  it('renders both cards, with Commercial standing before Relationship (FR-18 order)', async () => {
    installFakeBackend(routes())
    render()
    await ready()

    const commercial = screen.getAllByText(cardTitle('commercialStanding')).find((el) => el.tagName === 'DIV')!
    const relationship = screen.getAllByText(cardTitle('relationship')).find((el) => el.tagName === 'DIV')!
    expect(commercial.compareDocumentPosition(relationship) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    expect(within(card('commercialStanding')).getByText(i18n.t('contactDetail.overview.fields.paymentTerms'))).toBeInTheDocument()
    expect(within(card('commercialStanding')).getByText(i18n.t('contactDetail.overview.fields.iban'))).toBeInTheDocument()
    expect(within(card('relationship')).getByText(i18n.t('contactDetail.overview.fields.advisor'))).toBeInTheDocument()
    expect(within(card('relationship')).getByText(i18n.t('contactDetail.overview.fields.tags'))).toBeInTheDocument()
  })

  it('shows the stored advisor label and a website as a live link', async () => {
    installFakeBackend(routes({ advisorId: 'user-7', advisorLabel: 'Rey Ortiz', website: 'https://byron.example' }))
    render()
    await ready()

    expect(within(card('relationship')).getByText('Rey Ortiz')).toBeInTheDocument()
    const link = within(card('relationship')).getByRole('link', { name: 'https://byron.example' })
    expect(link).toHaveAttribute('href', 'https://byron.example')
  })

  it('toggling VAT-registered PATCHes the contact (a save from the Commercial standing card)', async () => {
    const backend = installFakeBackend([
      ...routes(),
      { method: 'PATCH', match: /^\/contacts\/c1$/, handler: (req) => contact({ id: 'c1', ...(req.body as object) }) },
    ])
    render()
    await ready()
    const user = userEvent.setup()

    await user.click(within(fieldRow('commercialStanding', 'vatRegistered')).getByRole('checkbox'))

    await waitFor(() => {
      const patches = backend.callsTo(/^\/contacts\/c1$/, 'PATCH')
      expect(patches.at(-1)!.body).toMatchObject({ vatRegistered: true })
    })
  })

  it('ticking VAT status confirmed PATCHes the contact (KAN-295)', async () => {
    const backend = installFakeBackend([
      ...routes(),
      { method: 'PATCH', match: /^\/contacts\/c1$/, handler: (req) => contact({ id: 'c1', ...(req.body as object) }) },
    ])
    render()
    await ready()
    const user = userEvent.setup()

    const box = within(fieldRow('commercialStanding', 'vatStatusConfirmed')).getByRole('checkbox')
    expect(box).not.toBeChecked()
    await user.click(box)

    await waitFor(() => {
      const patches = backend.callsTo(/^\/contacts\/c1$/, 'PATCH')
      expect(patches.at(-1)!.body).toEqual({ vatStatusConfirmed: true })
    })
  })

  it('adding a tag PATCHes the full tag list', async () => {
    const backend = installFakeBackend([
      ...routes({ tags: ['Oldtimer'] }),
      { method: 'PATCH', match: /^\/contacts\/c1$/, handler: (req) => contact({ id: 'c1', ...(req.body as object) }) },
    ])
    render()
    await ready()
    const user = userEvent.setup()

    const input = within(fieldRow('relationship', 'tags')).getByRole('textbox')
    await user.type(input, 'Flottenkunde{enter}')

    await waitFor(() => {
      const patches = backend.callsTo(/^\/contacts\/c1$/, 'PATCH')
      expect(patches.at(-1)!.body).toMatchObject({ tags: ['Oldtimer', 'Flottenkunde'] })
    })
  })

  it('title and gender show for an individual', async () => {
    installFakeBackend(routes())
    render()
    await ready()
    expect(within(card('identity')).getByText(i18n.t('contactDetail.overview.fields.title'))).toBeInTheDocument()
    expect(within(card('identity')).getByText(i18n.t('contactDetail.overview.fields.gender'))).toBeInTheDocument()
  })

  it('title and gender are absent for a business contact', async () => {
    installFakeBackend(routes({ contactType: 'business', firstName: null, lastName: null, companyName: 'Byron AG', title: null }))
    render()
    await ready()
    expect(within(card('identity')).getByText(i18n.t('contactDetail.overview.fields.companyName'))).toBeInTheDocument()
    expect(within(card('identity')).queryByText(i18n.t('contactDetail.overview.fields.gender'))).not.toBeInTheDocument()
  })
})
