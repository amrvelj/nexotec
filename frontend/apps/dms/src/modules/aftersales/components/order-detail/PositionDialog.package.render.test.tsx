// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend, status } from '../../../../test/fakeBackend'
import { PositionDialog } from './PositionDialog'
import { JOB_ID, ORDER_ID, order } from '../../test/serviceOrderFixtures'

// KAN-818 (D13 «Aus dem Katalog», check 44): the active packages, named in the UI language else as
// typed (ruling 176); the chosen one is copied into the job by the server, with the order's version.

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options)
const COPY = new RegExp(`^/service-orders/${ORDER_ID}/jobs/${JOB_ID}/service-packages$`)

const PACKAGES = {
  items: [
    {
      id: 'pkg-1',
      name: 'Service 30 000 km',
      nameTranslations: { de: 'Service 30’000 km' },
      isActive: true,
      positions: [
        {
          id: 'pp-1',
          sortIndex: 1,
          kind: 'labour',
          text: 'Service nach Herstellervorgabe',
          labourCategoryId: 'cat-1',
          labourCategoryLabel: 'Mechanik',
          time: { quantity: '1.50', unit: 'hours', workUnitsPerHour: null },
          checkNeeded: true,
        },
        { id: 'pp-2', sortIndex: 2, kind: 'text', text: 'Ölfilter', labourCategoryId: null, labourCategoryLabel: null, time: null, checkNeeded: false },
      ],
      version: 2,
      updatedAt: '2026-11-26T08:00:00Z',
    },
    { id: 'pkg-2', name: 'Klimaservice', nameTranslations: {}, isActive: true, positions: [], version: 1, updatedAt: '2026-11-26T08:00:00Z' },
  ],
}

function install(post: () => unknown = () => order()) {
  return installFakeBackend([
    { method: 'GET', match: /^\/dealerships\/dealership-1\/aftersales\/service-packages$/, handler: () => PACKAGES },
    { method: 'POST', match: COPY, handler: post },
  ])
}

function renderDialog() {
  const onSaved = vi.fn()
  renderWithProviders(
    <PositionDialog opened order={{ id: ORDER_ID, version: 3 }} jobId={JOB_ID} position={null} onClose={() => {}} onSaved={onSaved} onReload={() => {}} />,
  )
  return { onSaved }
}

const packageField = () => screen.findByRole('textbox', { name: (name) => name.startsWith(t('serviceOrder.positionDialog.package')) })

describe('the position dialog «Aus dem Katalog» (KAN-818)', () => {
  it('lists the active packages in the UI language, shows what is copied, and copies the chosen one with If-Match', async () => {
    const user = userEvent.setup()
    const backend = install()
    const { onSaved } = renderDialog()

    await user.click(screen.getByRole('radio', { name: t('serviceOrder.positionDialog.kinds.package') }))
    await user.click(await packageField())
    // Ruling 176: the German name, else as typed.
    expect(await screen.findByRole('option', { name: 'Service 30’000 km' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Klimaservice' })).toBeInTheDocument()
    expect(backend.callsTo(/service-packages$/, 'GET')[0].params.get('active')).toBe('true')
    await user.click(screen.getByRole('option', { name: 'Service 30’000 km' }))
    const preview = screen.getByTestId('package-positions')
    expect(within(preview).getByText('Ölfilter')).toBeInTheDocument()
    expect(within(preview).getByText((text) => text.startsWith('Service nach Herstellervorgabe ·'))).toBeInTheDocument()
    // No text field to type: the package brings its positions.
    expect(screen.queryByRole('textbox', { name: (name) => name.startsWith(t('serviceOrder.positionDialog.text')) })).toBeNull()

    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const [post] = backend.callsTo(COPY, 'POST')
    expect(post.body).toEqual({ servicePackageId: 'pkg-1' })
    expect(post.headers.get('If-Match')).toBe('3')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('says a package is missing instead of sending, and shows a refusal in the UI language', async () => {
    const user = userEvent.setup()
    const backend = install(() =>
      status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'service_package_labour_rate_missing' } } }),
    )
    renderDialog()

    await user.click(screen.getByRole('radio', { name: t('serviceOrder.positionDialog.kinds.package') }))
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    expect(await screen.findByText(t('serviceOrder.positionDialog.missing.package'))).toBeInTheDocument()
    expect(backend.callsTo(COPY, 'POST')).toHaveLength(0)

    await user.click(await packageField())
    await user.click(await screen.findByRole('option', { name: 'Klimaservice' }))
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    expect(await screen.findByText(t('apiRefusal.reasons.service_package_labour_rate_missing'))).toBeInTheDocument()
  })
})
