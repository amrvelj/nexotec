// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
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
