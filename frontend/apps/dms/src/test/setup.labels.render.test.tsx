// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

// KAN-325 — `src/test/setup.ts` resolves a `<label for>` through the
// document's id index instead of jsdom's walk of the whole tree from the
// root, which every `*ByRole({ name })` and `*ByLabelText` query paid once
// per label after any DOM change. These pin that the answers are the ones
// the HTML spec gives, so a query finds exactly what it found before. (They
// pass against jsdom's own walk too: what the patch changes is time, not
// the answer — measured in the ticket.)

const label = (id: string) => document.getElementById(id) as HTMLLabelElement

describe('setup — label.control through the id index (KAN-325)', () => {
  it('resolves `for` to the first element with that id in tree order, and to nothing when that element is not labelable', () => {
    document.body.innerHTML = `
      <label id="l-dup" for="dup">Dup</label>
      <div id="dup"></div>
      <input id="dup" />
      <label id="l-ok" for="ok">Ok</label>
      <input id="ok" />
      <label id="l-missing" for="missing">Missing</label>
      <label id="l-empty" for="">Empty</label>
      <label id="l-hidden" for="hidden">Hidden</label>
      <input id="hidden" type="hidden" />
      <label id="l-later" for="later">Later</label>
    `
    expect(label('l-dup').control).toBeNull()
    expect(label('l-ok').control).toBe(document.getElementById('ok'))
    expect(label('l-missing').control).toBeNull()
    expect(label('l-empty').control).toBeNull()
    expect(label('l-hidden').control).toBeNull()
    // the index follows the DOM: a control added later is found
    const later = document.createElement('textarea')
    later.id = 'later'
    document.body.append(later)
    expect(label('l-later').control).toBe(later)
  })

  it('without `for`, the first labelable descendant', () => {
    document.body.innerHTML = `
      <label id="l-nested"><span>Name</span><input id="nested" /><input id="second" /></label>
      <label id="l-none"><span>Nothing here</span></label>
    `
    expect(label('l-nested').control).toBe(document.getElementById('nested'))
    expect(label('l-none').control).toBeNull()
  })

  it('a label in a detached subtree resolves `for` within that subtree', () => {
    const fragment = document.createElement('div')
    fragment.innerHTML = '<label for="x">X</label><input id="x" />'
    expect(fragment.querySelector('label')!.control).toBe(fragment.querySelector('input'))
  })

  it('`input.labels` lists every label pointing at the input, in tree order, and follows DOM changes', () => {
    document.body.innerHTML = `
      <label id="a" for="field">A</label>
      <label id="b"><input id="field" /></label>
      <label id="c" for="field">C</label>
      <label id="d" for="other">D</label>
      <input id="other" />
    `
    const field = document.getElementById('field') as HTMLInputElement
    expect(Array.from(field.labels!).map((l) => l.id)).toEqual(['a', 'b', 'c'])
    label('d').htmlFor = 'field'
    expect(Array.from(field.labels!).map((l) => l.id)).toEqual(['a', 'b', 'c', 'd'])
    label('a').remove()
    expect(Array.from(field.labels!).map((l) => l.id)).toEqual(['b', 'c', 'd'])
  })

  it('a role query still names an input by its label', () => {
    render(
      <form>
        <label htmlFor="vin">Fahrgestellnummer</label>
        <input id="vin" />
        <label htmlFor="plate">Kontrollschild</label>
        <input id="plate" />
      </form>,
    )
    expect(screen.getByRole('textbox', { name: 'Kontrollschild' })).toBe(document.getElementById('plate'))
    expect(screen.getByLabelText('Fahrgestellnummer')).toBe(document.getElementById('vin'))
  })
})
