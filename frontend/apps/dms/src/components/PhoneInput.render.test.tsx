// @vitest-environment jsdom
import { Profiler, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { theme } from '@nexotec/ui-kit'
import i18n from '../i18n'
import { PHONE_INPUT_MIN_WIDTH, PhoneInput } from './PhoneInput'

// KAN-267 — Mantine's `Group grow` caps every child at half the row by
// default, so the country select never got its width and longer countries
// were cut off ("Liechtenstein (+42", "Vereinigtes Königr…"). jsdom has no
// layout: this pins the styles; the ticket's screenshots show the result in
// all four languages.

function renderPhone() {
  render(
    <MantineProvider theme={theme} env="test">
      <PhoneInput label={i18n.t('customerDetail.phoneInput.country')} value="+447911123456" onChange={() => {}} />
    </MantineProvider>,
  )
  const number = screen.getByLabelText(i18n.t('customerDetail.phoneInput.number'))
  const numberRoot = number.closest('.mantine-TextInput-root') as HTMLElement
  return { group: numberRoot.parentElement as HTMLElement, numberRoot }
}

describe('PhoneInput width (KAN-267)', () => {
  it('lets the country select keep its width: no half-row cap on the group', () => {
    const { group } = renderPhone()

    expect(group.style.getPropertyValue('--group-child-width')).toBe('')
  })

  it('moves the number under the country rather than squeezing it, and its minimum fits the advertised editor width', () => {
    const { numberRoot } = renderPhone()

    expect(numberRoot.style.flex).toBe('1 1 150px')
    expect(numberRoot.style.minWidth).toBe('150px')
    expect(PHONE_INPUT_MIN_WIDTH).toBe(272 + 10 + 150)
  })
})

// KAN-167 — the re-split of the external value ran in an effect, so a
// keystroke whose emitted value came back re-split (a digit typed after a
// space) committed twice: once for the typed text, once more when the effect
// set the split state. It is now
// adjusted during render, before the children render. What the input shows
// is unchanged: the national form with its trunk 0.

function StatefulPhone({ initial, onCommit }: { initial: string; onCommit: () => void }) {
  const [value, setValue] = useState(initial)
  return (
    <MantineProvider theme={theme} env="test">
      <Profiler id="phone" onRender={onCommit}>
        <PhoneInput label={i18n.t('customerDetail.phoneInput.country')} value={value} onChange={setValue} />
      </Profiler>
      <output data-testid="emitted">{value}</output>
      <button type="button" onClick={() => setValue('+41443330000')}>
        load
      </button>
    </MantineProvider>
  )
}

describe('PhoneInput renders (KAN-167)', () => {
  it('commits once per keystroke, beside the Select re-sync, also where the re-split changes what is shown', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<StatefulPhone initial="" onCommit={onCommit} />)
    const number = screen.getByLabelText(i18n.t('customerDetail.phoneInput.number')) as HTMLInputElement
    await user.click(number)
    onCommit.mockClear()

    // Typed as people write it; every digit after a space comes back
    // re-split without the spaces, which used to cost a second commit.
    const typed = '079 123 45 67'
    await user.keyboard(typed)

    // Per keystroke: PhoneInput's one commit, plus the re-sync Mantine's
    // Select runs in a layout effect after any commit of its own (React's
    // "nested-update" — the kit's, not PhoneInput's). The effect this
    // replaced added a third commit on every digit typed after a space
    // (29 here, where 26 is the floor).
    expect(onCommit).toHaveBeenCalledTimes(2 * typed.length)
    expect(onCommit.mock.calls.filter(([, phase]) => phase === 'update')).toHaveLength(typed.length)
    expect(number.value).toBe('0791234567')
    expect(screen.getByTestId('emitted').textContent).toBe('+41791234567')
  })

  it('shows a loaded value in national form and keeps the trunk 0 while typing', async () => {
    const user = userEvent.setup()
    render(<StatefulPhone initial="+41791110000" onCommit={() => {}} />)
    const number = screen.getByLabelText(i18n.t('customerDetail.phoneInput.number')) as HTMLInputElement
    expect(number.value).toBe('0791110000')

    await user.clear(number)
    await user.type(number, '0761')
    expect(screen.getByTestId('emitted').textContent).toBe('+41761')
    expect(number.value).toBe('0761')

    await user.click(screen.getByRole('button', { name: 'load' }))
    expect(number.value).toBe('0443330000')
  })
})
