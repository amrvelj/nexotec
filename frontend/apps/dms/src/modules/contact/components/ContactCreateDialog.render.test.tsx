// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes, useLocation } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import en from '../../../i18n/locales/en'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { installFakeBackend, status } from '../../../test/fakeBackend'
import { contact, contactPage } from '../../../test/fixtures'
import { ContactCreateFlow } from './ContactCreateFlow'
import { ContactCreatePage } from '../pages/ContactCreatePage'
import { ContactsListPage } from '../pages/ContactsListPage'

// KAN-48 — FR-13: "All four languages are first-class. No fallback-to-
// German (here: fallback-to-English) placeholders in production." The
// create form was ~21 hardcoded English strings and a full-page route;
// this proves it is translated in every bundle and opens as a dialog
// (FR-05 / FR-20).

// jsdom has no layout → TanStack Virtual windows to 0 rows; render them all
// so the "dialog opens over the list, list stays intact" assertions can see
// the grid rows (same shim ContactsListPage.columns.render.test.tsx uses).
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

// Distinctive multi-word English phrases from contactCreate that no
// DE/FR/IT translation would ever reproduce verbatim — their presence in a
// non-English render means a key fell through to the `en` fallback.
const ENGLISH_ONLY_PHRASES = [
  en.contactCreate.type.individual.description,
  en.contactCreate.type.business.description,
  en.contactCreate.fields.correspondenceLanguage,
  en.contactCreate.fields.firstName,
  en.contactCreate.fields.lastName,
  en.contactCreate.fields.preferredChannel,
  en.contactCreate.fields.lifecycleStatus,
  en.contactCreate.fields.hasAddress,
  en.contactCreate.fields.marketingConsent,
  en.contactCreate.fields.houseNumber,
  en.contactCreate.descriptions.correspondenceLanguage,
  en.contactCreate.actions.submit,
  en.contactCreate.validation.individualNameRequired,
  en.contactCreate.validation.contactPointRequired,
  en.contactCreate.duplicates.exactMatch,
  en.contactCreate.duplicates.openInstead,
]

const DUPLICATE_CANDIDATE = {
  id: 'dup-1',
  contactNumber: 'K-000001',
  contactType: 'individual',
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
    { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
    { method: 'POST', match: /^\/contacts$/, handler: (req) => ({ id: 'new-1', ...(req.body as object) }) },
  ])
}

async function advanceToDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.next') }))
  await screen.findByText(i18n.t('contactDetail.contactPoints.phoneNumbers'))
}

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('ContactCreateFlow — every string is translated (KAN-48, FR-13)', () => {
  for (const lng of ['fr', 'it', 'de'] as const) {
    it(`renders the individual branch in ${lng} with no English leak and no missing-key marker`, async () => {
      await i18n.changeLanguage(lng)
      const user = userEvent.setup()
      installCreateBackend()
      const { container } = renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

      // step 1 — the two type cards
      expect(screen.getByText(i18n.t('contactCreate.type.individual.label'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('contactCreate.type.business.label'))).toBeInTheDocument()

      await advanceToDetails(user)
      await user.click(screen.getByLabelText(i18n.t('contactCreate.fields.hasAddress')))

      expect(container.textContent).not.toMatch(MARKER)
      for (const phrase of ENGLISH_ONLY_PHRASES) {
        expect(screen.queryByText(phrase), `"${phrase}" leaked in ${lng}`).toBeNull()
      }

      // positive — a representative label resolves to the active bundle
      expect(screen.getByText(i18n.t('contactCreate.fields.correspondenceLanguage'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('contactCreate.fields.marketingConsent'))).toBeInTheDocument()
    })
  }

  it('renders the business branch in fr with no English leak (company-only fields mount)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installCreateBackend()
    const { container } = renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await user.click(screen.getByText(i18n.t('contactCreate.type.business.label')))
    await advanceToDetails(user)

    expect(screen.getByText(i18n.t('contactCreate.fields.companyName'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('contactCreate.fields.legalForm'))).toBeInTheDocument()
    expect(container.textContent).not.toMatch(MARKER)
    expect(screen.queryByText('Company name')).toBeNull()
  })

  // KAN-104 item 4: the legal-form options were hardcoded German in every
  // language. In French they read SA / Sàrl (the French names of AG / GmbH).
  it('offers the legal forms in the UI language', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installCreateBackend()
    renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await user.click(screen.getByText(i18n.t('contactCreate.type.business.label')))
    await advanceToDetails(user)
    await user.click(screen.getByRole('textbox', { name: i18n.t('contactCreate.fields.legalForm') }))

    for (const label of ['SA', 'Sàrl', 'Entreprise individuelle', 'Association', 'Société coopérative', 'Autre']) {
      expect(await screen.findByRole('option', { name: label })).toBeInTheDocument()
    }
    expect(screen.queryByRole('option', { name: 'Einzelfirma' })).toBeNull()
  })

  it('the numbered step header comes from the bundle, not a hardcoded const (exit criterion 2)', async () => {
    // The step labels used to be a module-level `const STEPS` of literal
    // English. In `de` the first step is "Typ", proving it now resolves
    // through t() — a hardcoded "Type" could never localise.
    await i18n.changeLanguage('de')
    installCreateBackend()
    renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    expect(i18n.t('contactCreate.steps.type')).toBe('Typ')
    expect(screen.getByText('Typ')).toBeInTheDocument()
    expect(screen.queryByText('Type')).toBeNull()
  })

  it('a submit-time validation message renders in the active language (exit criterion 4)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installCreateBackend()
    renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await advanceToDetails(user)
    // no name, no contact point → submit surfaces the individual-name rule
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.submit') }))

    const message = i18n.t('contactCreate.validation.individualNameRequired')
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(message).not.toBe(en.contactCreate.validation.individualNameRequired)
    expect(screen.queryByText(en.contactCreate.validation.individualNameRequired)).toBeNull()
  })

  it('the duplicate-warning panel is localised too (FR-04 + FR-13)', async () => {
    await i18n.changeLanguage('fr')
    const user = userEvent.setup()
    installFakeBackend([
      { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [DUPLICATE_CANDIDATE], nextCursor: null }) },
      { method: 'POST', match: /^\/contacts$/, handler: (req) => ({ id: 'x', ...(req.body as object) }) },
    ])
    const { container } = renderWithProviders(
      <ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} onOpenExisting={() => {}} />,
    )

    await advanceToDetails(user)
    // the dedupe effect fires off the last name; type it so a candidate returns
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')

    expect(await screen.findByText(i18n.t('contactCreate.duplicates.openInstead'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('contactCreate.duplicates.exactMatch'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('contactCreate.duplicates.heading', { count: 1 }))).toBeInTheDocument()
    expect(container.textContent).not.toMatch(MARKER)
    expect(screen.queryByText('Open instead')).toBeNull()
    expect(screen.queryByText('Exact match')).toBeNull()
    // KAN-302 — the candidate's lifecycle badge is translated too, never the
    // ui-kit's English default nor the English bundle's label.
    const candidate = screen.getByText('Hans Muster').closest('button') as HTMLElement
    expect(within(candidate).getByText(i18n.t('contactEnums.lifecycleStatus.active'))).toBeInTheDocument()
    expect(i18n.t('contactEnums.lifecycleStatus.active')).not.toBe(en.contactEnums.lifecycleStatus.active)
    expect(within(candidate).queryByText(en.contactEnums.lifecycleStatus.active)).toBeNull()
  })

  it('correspondence language pre-fills from the acting user\'s UI language (FR-03)', async () => {
    await i18n.changeLanguage('fr')
    installCreateBackend()
    const { container } = renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await advanceToDetails(userEvent.setup())
    // LANGUAGE_OPTIONS endonyms — fr UI seeds the Select to "Français"
    const langInput = container.querySelector<HTMLInputElement>('input[data-path="language"]')
    expect(langInput?.value).toBe('Français')
  })

  it('the business-branch validation message is localised (exit criterion 4)', async () => {
    await i18n.changeLanguage('it')
    const user = userEvent.setup()
    installCreateBackend()
    renderWithProviders(<ContactCreateFlow onSuccess={() => {}} onCancel={() => {}} />)

    await user.click(screen.getByText(i18n.t('contactCreate.type.business.label')))
    await advanceToDetails(user)
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.submit') }))

    const message = i18n.t('contactCreate.validation.companyNameRequired')
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.queryByText(en.contactCreate.validation.companyNameRequired)).toBeNull()
  })

  it('the meaningful field labels are genuinely translated, not English copies (fr/it)', async () => {
    // KAN-438: fr/it load on demand; load the bundles the values are read from.
    await i18n.loadLanguages(['fr', 'it'])
    // localeKeyParity guarantees the keys exist; this guards the values for
    // the labels that carry meaning (short tokens like "Type"/"Source"/"UID"
    // are legitimately identical across languages and are not checked).
    const checked = [
      'contactCreate.title',
      'contactCreate.fields.correspondenceLanguage',
      'contactCreate.fields.firstName',
      'contactCreate.fields.lastName',
      'contactCreate.fields.companyName',
      'contactCreate.fields.marketingConsent',
      'contactCreate.fields.hasAddress',
      'contactCreate.actions.submit',
      'contactCreate.validation.individualNameRequired',
      'contactCreate.validation.contactPointRequired',
      'contactCreate.errors.createFailed',
      'contactCreate.type.individual.description',
      'contactCreate.duplicates.heading_one',
      'contactCreate.duplicates.exactMatch',
      'contactCreate.duplicates.openInstead',
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

describe('ContactCreateFlow — one Idempotency-Key per submission (KAN-266)', () => {
  it('a submit retried after a failure carries the same key, so the server replays instead of creating twice', async () => {
    const user = userEvent.setup()
    const created = vi.fn()
    let posts = 0
    const backend = installFakeBackend([
      { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /^\/contacts$/,
        handler: (req) => {
          posts += 1
          if (posts === 1) return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
          return status(201, { id: 'new-1', ...(req.body as object) })
        },
      },
    ])
    const { container } = renderWithProviders(<ContactCreateFlow onSuccess={created} onCancel={() => {}} />)

    await advanceToDetails(user)
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="firstName"]')!, 'Anna')
    await user.type(container.querySelector<HTMLInputElement>('input[data-path="lastName"]')!, 'Muster')
    await user.click(screen.getByRole('button', { name: new RegExp(i18n.t('contactDetail.contactPoints.addEmail'), 'i') }))
    await user.type(container.querySelector<HTMLInputElement>('input[type="email"]')!, 'anna@example.ch')
    await user.click(screen.getByRole('button', { name: i18n.t('contactDetail.contactPoints.save') }))
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.submit') }))
    expect(await screen.findByText(i18n.t('contactCreate.errors.createFailed'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18n.t('contactCreate.actions.submit') }))

    await waitFor(() => expect(created).toHaveBeenCalledTimes(1))
    const keys = backend.callsTo(/^\/contacts$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBe(keys[0])
  })
})

describe('the create surface is a dialog (KAN-48, FR-05 / FR-20)', () => {
  function installListBackend(rows = [contact({ id: 'c1', contactNumber: 'K-000042', lastName: 'Meier' })]) {
    return installFakeBackend([
      { match: /^\/contacts$/, handler: () => contactPage(rows) },
      { match: /^\/contacts\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
      { method: 'POST', match: /^\/contacts$/, handler: (req) => ({ id: 'new-9', ...(req.body as object) }) },
    ])
  }

  it('the "New contact" button opens a dialog over the list, and cancelling leaves the list intact', async () => {
    const user = userEvent.setup()
    installListBackend()
    renderWithProviders(
      <>
        <LocationProbe />
        <ContactsListPage />
      </>,
      { route: '/contacts' },
    )

    expect(await screen.findByText('K-000042')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: i18n.t('contactsList.newContact') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('contactCreate.type.individual.label'))).toBeInTheDocument()
    // opening the dialog did NOT push a route
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/contacts$/)

    await user.click(within(dialog).getByRole('button', { name: i18n.t('contactCreate.actions.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // the list never unmounted and the URL never changed
    expect(screen.getByText('K-000042')).toBeInTheDocument()
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/contacts$/)
  })

  it('/contacts/new resolves directly to the dialog, and closing returns to the list', async () => {
    const user = userEvent.setup()
    installListBackend()
    renderWithProviders(
      <Routes>
        <Route path="/contacts" element={<><LocationProbe /><ContactsListPage /></>} />
        <Route path="/contacts/new" element={<><LocationProbe /><ContactCreatePage /></>} />
      </Routes>,
      { route: '/contacts/new' },
    )

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('contactCreate.steps.type'))).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: i18n.t('contactCreate.actions.cancel') }))
    await screen.findByText('K-000042')
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/contacts$/)
  })

  it('ContactCreateDialog is the single create surface — Sales and Valuation open it, not a thinner copy (FR-20)', () => {
    const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8')
    // Sales mounts it in the deal's contact container, shared by the offer
    // workspace and a pending contract since KAN-307.
    for (const rel of [
      '../../sales/components/deal-workspace/DealWorkspaceContainers.tsx',
      '../../valuation/components/ValuationCreateDialog.tsx',
      '../pages/ContactsListPage.tsx',
    ]) {
      const src = read(rel)
      expect(src, `${rel} should mount ContactCreateDialog`).toContain('ContactCreateDialog')
      expect(src, `${rel} should not wrap ContactCreateFlow itself`).not.toMatch(/<ContactCreateFlow[\s/>]/)
    }
    // and exactly one file defines the two-step wizard
    expect(read('./ContactCreateFlow.tsx')).toMatch(/contactCreate\.steps\.type/)
  })
})
