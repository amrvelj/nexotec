// @vitest-environment jsdom
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { Select } from '@mantine/core'
import { renderWithProviders } from './renderWithProviders'
import { installFakeBackend } from './fakeBackend'
import { pickOptionWithoutOpening } from './pickOption'

// KAN-579, option (b): the fixtures pick a Select's value without opening its
// dropdown. These pin what that shortcut relies on and what it refuses.

function Picker({ onChange }: { onChange?: (value: string | null) => void }) {
  const [value, setValue] = useState<string | null>(null)
  return (
    <>
      <Select
        label="Treibstoff"
        data={[
          { value: 'petrol', label: 'Benzin' },
          { value: 'diesel', label: 'Diesel' },
        ]}
        value={value}
        onChange={(next) => {
          setValue(next)
          onChange?.(next)
        }}
      />
      <Select label="Getriebe" data={[{ value: 'manual', label: 'Schaltgetriebe' }]} />
    </>
  )
}

describe('pickOptionWithoutOpening (KAN-579)', () => {
  it('sets the value through the Select’s own submit and never opens the dropdown', async () => {
    installFakeBackend([])
    const picked: (string | null)[] = []
    renderWithProviders(<Picker onChange={(v) => picked.push(v)} />)
    const field = screen.getByRole('textbox', { name: 'Treibstoff' })
    const expanded: (string | null)[] = []
    const watcher = new MutationObserver(() => expanded.push(field.getAttribute('aria-expanded')))
    watcher.observe(field, { attributes: true, attributeFilter: ['aria-expanded'] })

    await pickOptionWithoutOpening('Diesel')

    await waitFor(() => expect(field).toHaveValue('Diesel'))
    watcher.disconnect()
    expect(picked).toEqual(['diesel'])
    expect(expanded).not.toContain('true')
    // a closed Select names no listbox: the dropdown was never open
    expect(field).not.toHaveAttribute('aria-controls')
  })

  it('refuses a label no option carries, naming it', async () => {
    installFakeBackend([])
    renderWithProviders(<Picker />)

    await expect(pickOptionWithoutOpening('Elektro', { timeout: 200 })).rejects.toThrow(/«Elektro».*found 0/)
  })

  it('refuses a label two fields offer, rather than pick the wrong field', async () => {
    installFakeBackend([])
    renderWithProviders(
      <>
        <Picker />
        <Select label="Treibstoff (Zweitfahrzeug)" data={[{ value: 'petrol', label: 'Benzin' }]} />
      </>,
    )

    await expect(pickOptionWithoutOpening('Benzin', { timeout: 200 })).rejects.toThrow(/«Benzin».*found 2/)
  })
})
