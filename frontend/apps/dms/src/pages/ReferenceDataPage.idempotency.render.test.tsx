// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import { ReferenceDataPage } from './ReferenceDataPage'

// KAN-119, exit criterion 5: a create mutation sends one Idempotency-Key per
// form submission — the same key when the user retries a failed submit (so
// the server can replay a create that did happen), a fresh key once the
// dialog is opened again for the next value.

describe('ReferenceDataPage — Idempotency-Key per submission', () => {
  it('retries under the same key and starts the next value with a new one', async () => {
    const user = userEvent.setup()
    let posts = 0
    const backend = installFakeBackend([
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /\/reference-data\/fuel_type$/,
        handler: (req) => {
          posts += 1
          if (posts === 1) {
            return status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } })
          }
          return status(201, { id: `value-${posts}`, ...(req.body as object), active: true, sortOrder: 0, version: 1 })
        },
      },
    ])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

    const openDialog = async () => {
      await user.click(await screen.findByRole('button', { name: i18n.t('referenceData.create.trigger') }))
      const dialog = within(await screen.findByRole('dialog'))
      await user.type(dialog.getByLabelText(i18n.t('referenceData.columns.code'), { exact: false }), 'diesel')
      for (const language of ['Deutsch', 'Français', 'Italiano', 'English']) {
        await user.type(dialog.getByLabelText(language, { exact: false }), 'Diesel')
      }
    }
    const submit = async () =>
      user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: i18n.t('referenceData.create.submit') }))
    const postKeys = () =>
      backend.callsTo(/\/reference-data\/fuel_type$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

    await openDialog()
    await submit()
    expect(await screen.findByText(i18n.t('referenceData.create.error'))).toBeInTheDocument()
    await submit()
    await waitFor(() => expect(postKeys()).toHaveLength(2))

    await openDialog()
    await submit()
    await waitFor(() => expect(postKeys()).toHaveLength(3))

    const [first, retry, next] = postKeys()
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
    expect(next).toBeTruthy()
    expect(next).not.toBe(first)
  })

  it('gives a dialog reopened after a failed submit a new key: a new form is a new submission', async () => {
    const user = userEvent.setup()
    const backend = installFakeBackend([
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
      {
        method: 'POST',
        match: /\/reference-data\/fuel_type$/,
        handler: () => status(503, { error: { code: 'unavailable', message: 'Try again.', details: null } }),
      },
    ])
    renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })
    const postKeys = () =>
      backend.callsTo(/\/reference-data\/fuel_type$/, 'POST').map((call) => call.headers.get('Idempotency-Key'))

    for (let attempt = 1; attempt <= 2; attempt++) {
      await user.click(await screen.findByRole('button', { name: i18n.t('referenceData.create.trigger') }))
      const dialog = within(await screen.findByRole('dialog'))
      await user.type(dialog.getByLabelText(i18n.t('referenceData.columns.code'), { exact: false }), 'diesel')
      await user.click(dialog.getByRole('button', { name: i18n.t('referenceData.create.submit') }))
      expect(await dialog.findByText(i18n.t('referenceData.create.error'))).toBeInTheDocument()
      await user.click(dialog.getByRole('button', { name: i18n.t('common.cancel') }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    }

    const [first, afterReopen] = postKeys()
    expect(first).toBeTruthy()
    expect(afterReopen).toBeTruthy()
    expect(afterReopen).not.toBe(first)
  })

  // KAN-437: a refused value is said in the UI language — a value code the
  // list already holds names the code, a schema 422 marks the field it
  // names — never the API's English text.
  it('says a duplicate code and a refused field in Italian, never in the API\'s English text', async () => {
    await i18n.changeLanguage('it')
    try {
      const user = userEvent.setup()
      let posts = 0
      installFakeBackend([
        { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
        {
          method: 'POST',
          match: /\/reference-data\/fuel_type$/,
          handler: () => {
            posts += 1
            if (posts === 1) {
              return status(409, {
                error: { code: 'conflict', message: "Reference value 'diesel' already exists in list 'fuel_type'.", details: { listCode: 'fuel_type', valueCode: 'diesel' } },
              })
            }
            return status(422, {
              error: {
                code: 'unprocessable_entity',
                message: 'Request validation failed.',
                details: { errors: [{ loc: ['body', 'labelIt'], msg: 'String should have at most 200 characters', type: 'string_too_long' }] },
              },
            })
          },
        },
      ])
      renderWithProviders(<ReferenceDataPage />, { route: '/settings/reference' })

      await user.click(await screen.findByRole('button', { name: i18n.t('referenceData.create.trigger') }))
      const dialog = within(await screen.findByRole('dialog'))
      await user.type(dialog.getByLabelText(i18n.t('referenceData.columns.code'), { exact: false }), 'diesel')
      for (const language of ['Deutsch', 'Français', 'Italiano', 'English']) {
        await user.type(dialog.getByLabelText(language, { exact: false }), 'Diesel')
      }
      const submit = () => user.click(dialog.getByRole('button', { name: i18n.t('referenceData.create.submit') }))

      await submit()
      expect(await dialog.findByText(i18n.t('referenceData.create.duplicate', { valueCode: 'diesel' }))).toBeInTheDocument()
      expect(dialog.queryByText(/already exists/)).not.toBeInTheDocument()

      await submit()
      expect(await dialog.findByText(i18n.t('referenceData.create.invalidField'))).toBeInTheDocument()
      expect(dialog.getByLabelText('Italiano', { exact: false })).toHaveAttribute('aria-invalid', 'true')
      expect(dialog.queryByText(/validation failed|at most 200/)).not.toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})
