// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import en from '../i18n/locales/en.json'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { customer, customerPage } from '../test/fixtures'
import { CustomerCreateFlow } from './CustomerCreateFlow'
import { CustomerCreatePage } from '../pages/CustomerCreatePage'
import { CustomersListPage } from '../pages/CustomersListPage'

// KAN-48 — FR-13: "All four languages are first-class. No fallback-to-
// German (here: fallback-to-English) placeholders in production." The
// create form was ~21 hardcoded English strings and a full-page route;
// this proves it is translated in every bundle and opens as a dialog
// (FR-05 / FR-20).

// jsdom has no layout → TanStack Virtual windows to 0 rows; render them all
// so the "dialog opens over the list, list stays intact" assertions can see
// the grid rows (same shim CustomersListPage.columns.render.test.tsx uses).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const MARKER = /⚠ MISSING I18N KEY/

// Distinctive multi-word English phrases from customerCreate that no
// DE/FR/IT translation would ever reproduce verbatim — their presence in a
// non-English render means a key fell through to the `en` fallback.
const ENGLISH_ONLY_PHRASES = [
  en.customerCreate.type.individual.description,
  en.customerCreate.type.business.description,
  en.customerCreate.fields.correspondenceLanguage,
  en.customerCreate.fields.firstName,
  en.customerCreate.fields.lastName,
  en.customerCreate.fields.preferredChannel,
  en.customerCreate.fields.lifecycleStatus,
  en.customerCreate.fields.hasAddress,
  en.customerCreate.fields.marketingConsent,
  en.customerCreate.fields.houseNumber,
  en.customerCreate.descriptions.correspondenceLanguage,
  en.customerCreate.actions.submit,
  en.customerCreate.validation.individualNameRequired,
  en.customerCreate.validation.contactPointRequired,
  en.customerCreate.duplicates.exactMatch,
  en.customerCreate.duplicates.openInstead,
]

const DUPLICATE_CANDIDATE = {
  id: 'dup-1',
  customerNumber: 'K-000001',
  customerType: 'individual',
  firstName: 'Hans',
  lastName: 'Muster',
  companyName: null,
  match: 'exact',
  lifecycleStatus: 'active',
  primaryPhone: '+41 79 111 00 00',
  primaryEmail: null,
}

function installCreateBackend() {
  return installFakeBackend([
    { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
  ])
}

async function advanceToDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
  await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('CustomerCreateFlow — every string is translated (KAN-48, FR-13)', () => {
  for (const lng of ['fr', 'it', 'de'] as const) {
    it(`renders the individual branch in ${lng} with no English leak and no missing-key marker`, async () => {
      await i18n.changeLanguage(lng)
      const user = userEvent.setup()
      installCreateBackend()
      const { container } = renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

      // step 1 — the two type cards
      expect(screen.getByText(i18n.t('customerCreate.type.individual.label'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('customerCreate.type.business.label'))).toBeInTheDocument()

      await advanceToDetails(user)
      await user.click(screen.getByLabelText(i18n.t('customerCreate.fields.hasAddress')))

      expect(container.textContent).not.toMatch(MARKER)
      for (const phrase of ENGLISH_ONLY_PHRASES) {
        expect(screen.queryByText(phrase), `"${phrase}" leaked in ${lng}`).toBeNull()
      }

      // positive — a representative label resolves to the active bundle
      expect(screen.getByText(i18n.t('customerCreate.fields.correspondenceLanguage'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('customerCreate.fields.marketingConsent'))).toBeInTheDocument()
    })
  }

  it('renders the business branch in fr with no English leak (company-only fields mount)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installCreateBackend()
    const { container } = renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await user.click(screen.getByText(i18n.t('customerCreate.type.business.label')))
    await advanceToDetails(user)

    expect(screen.getByText(i18n.t('customerCreate.fields.companyName'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('customerCreate.fields.legalForm'))).toBeInTheDocument()
    expect(container.textContent).not.toMatch(MARKER)
    expect(screen.queryByText('Company name')).toBeNull()
  })

  it('the numbered step header comes from the bundle, not a hardcoded const (exit criterion 2)', async () => {
    // The step labels used to be a module-level `const STEPS` of literal
    // English. In `de` the first step is "Typ", proving it now resolves
    // through t() — a hardcoded "Type" could never localise.
    await i18n.changeLanguage('de')
    installCreateBackend()
    renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    expect(i18n.t('customerCreate.steps.type')).toBe('Typ')
    expect(screen.getByText('Typ')).toBeInTheDocument()
    expect(screen.queryByText('Type')).toBeNull()
  })

  it('a submit-time validation message renders in the active language (exit criterion 4)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installCreateBackend()
    renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await advanceToDetails(user)
    // no name, no contact point → submit surfaces the individual-name rule
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.submit') }))

    const message = i18n.t('customerCreate.validation.individualNameRequired')
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(message).not.toBe(en.customerCreate.validation.individualNameRequired)
    expect(screen.queryByText(en.customerCreate.validation.individualNameRequired)).toBeNull()
  })

  it('the duplicate-warning panel is localised too (FR-04 + FR-13)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([
      { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [DUPLICATE_CANDIDATE], nextCursor: null }) },
      { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'x', ...(req.body as object) }) },
    ])
    const { container } = renderWithProviders(
      <CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} onOpenExisting={() => {}} />,
    )

    await advanceToDetails(user)
    // the dedupe effect fires off the last name; type it so a candidate returns
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')

    expect(await screen.findByText(i18n.t('customerCreate.duplicates.openInstead'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('customerCreate.duplicates.exactMatch'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('customerCreate.duplicates.heading', { count: 1 }))).toBeInTheDocument()
    expect(container.textContent).not.toMatch(MARKER)
    expect(screen.queryByText('Open instead')).toBeNull()
    expect(screen.queryByText('Exact match')).toBeNull()
  })

  it('correspondence language pre-fills from the acting user\'s UI language (FR-03)', async () => {
    await i18n.changeLanguage('fr')
    installCreateBackend()
    const { container } = renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await advanceToDetails(userEvent.setup())
    // LANGUAGE_OPTIONS endonyms — fr UI seeds the Select to "Français"
    const langInput = container.querySelector<HTMLInputElement>('input[data-path="language"]')
    expect(langInput?.value).toBe('Français')
  })

  it('the business-branch validation message is localised (exit criterion 4)', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    installCreateBackend()
    renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await user.click(screen.getByText(i18n.t('customerCreate.type.business.label')))
    await advanceToDetails(user)
    await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.submit') }))

    const message = i18n.t('customerCreate.validation.companyNameRequired')
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.queryByText(en.customerCreate.validation.companyNameRequired)).toBeNull()
  })

  it('the meaningful field labels are genuinely translated, not English copies (fr/it)', () => {
    // localeKeyParity guarantees the keys exist; this guards the values for
    // the labels that carry meaning (short tokens like "Type"/"Source"/"UID"
    // are legitimately identical across languages and are not checked).
    const checked = [
      'customerCreate.title',
      'customerCreate.fields.correspondenceLanguage',
      'customerCreate.fields.firstName',
      'customerCreate.fields.lastName',
      'customerCreate.fields.companyName',
      'customerCreate.fields.marketingConsent',
      'customerCreate.fields.hasAddress',
      'customerCreate.actions.submit',
      'customerCreate.validation.individualNameRequired',
      'customerCreate.validation.contactPointRequired',
      'customerCreate.errors.createFailed',
      'customerCreate.type.individual.description',
      'customerCreate.duplicates.heading_one',
      'customerCreate.duplicates.exactMatch',
      'customerCreate.duplicates.openInstead',
    ]
    for (const lng of ['fr', 'it'] as const) {
      const copied = checked.filter((key) => i18n.getFixedT(lng)(key) === i18n.getFixedT('en')(key))
      expect(copied, `${lng}.json has English copies at: ${copied.join(', ')}`).toEqual([])
    }
  })
})

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="loc">{location.pathname}</div>
}

describe('the create surface is a dialog (KAN-48, FR-05 / FR-20)', () => {
  function installListBackend(rows = [customer({ id: 'c1', customerNumber: 'K-000042', lastName: 'Meier' })]) {
    return installFakeBackend([
      { match: /^\/customers$/, handler: () => customerPage(rows) },
      { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/customers$/, handler: (req) => ({ id: 'new-9', ...(req.body as object) }) },
    ])
  }

  it('the "New customer" button opens a dialog over the list, and cancelling leaves the list intact', async () => {
    const user = userEvent.setup()
    installListBackend()
    renderWithProviders(
      <>
        <LocationProbe />
        <CustomersListPage />
      </>,
      { route: '/customers' },
    )

    expect(await screen.findByText('K-000042')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('customersList.newCustomer') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('customerCreate.type.individual.label'))).toBeInTheDocument()
    // opening the dialog did NOT push a route
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/customers$/)

    await user.click(within(dialog).getByRole('button', { name: i18n.t('customerCreate.actions.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // the list never unmounted and the URL never changed
    expect(screen.getByText('K-000042')).toBeInTheDocument()
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/customers$/)
  })

  it('/customers/new resolves directly to the dialog, and closing returns to the list', async () => {
    const user = userEvent.setup()
    installListBackend()
    renderWithProviders(
      <Routes>
        <Route path="/customers" element={<><LocationProbe /><CustomersListPage /></>} />
        <Route path="/customers/new" element={<><LocationProbe /><CustomerCreatePage /></>} />
      </Routes>,
      { route: '/customers/new' },
    )

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('customerCreate.steps.type'))).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: i18n.t('customerCreate.actions.cancel') }))
    await screen.findByText('K-000042')
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/customers$/)
  })

  it('CustomerCreateDialog is the single create surface — Sales and Valuation open it, not a thinner copy (FR-20)', () => {
    const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8')
    for (const rel of ['../pages/OfferWorkspacePage.tsx', './ValuationCreateDialog.tsx', '../pages/CustomersListPage.tsx']) {
      const src = read(rel)
      expect(src, `${rel} should mount CustomerCreateDialog`).toContain('CustomerCreateDialog')
      expect(src, `${rel} should not wrap CustomerCreateFlow itself`).not.toMatch(/<CustomerCreateFlow[\s/>]/)
    }
    // and exactly one file defines the two-step wizard
    expect(read('./CustomerCreateFlow.tsx')).toMatch(/customerCreate\.steps\.type/)
  })
})
