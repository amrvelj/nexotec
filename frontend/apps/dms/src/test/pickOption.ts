import { fireEvent, waitFor } from '@testing-library/react'

/**
 * KAN-579, option (b) (architect's call, 2026-11-11): render-test fixtures
 * pick a Mantine `Select`'s value WITHOUT opening its dropdown.
 *
 * Mantine 7 mounts every Select's dropdown hidden (`keepMounted`), with its
 * options in the DOM. Opening one is what costs a render test its time:
 * floating-ui positions the dropdown and Mantine queries the document,
 * 70–90 ms per pick (KAN-335's measurement). Clicking the hidden option runs
 * the same `onOptionSubmit` a user's click runs, so the field takes the
 * value through the production code path; only the opening is skipped. The
 * production DOM is untouched (no `keepMounted` change), so what a screen
 * reader and the user meet stays exactly as it is.
 *
 * A closed Select carries no link to its listbox (`aria-controls` is set
 * only while it is open), so the option is found by its label across the
 * document and must be the only one with that label — otherwise this throws,
 * naming the label, rather than pick another field's option. The tests that
 * are about opening a dropdown keep doing it with `userEvent`.
 *
 * It only picks: what the field shows next is the caller's to assert (a
 * save the server refuses puts the old value back).
 */
export async function pickOptionWithoutOpening(
  optionLabel: string,
  { timeout }: { timeout?: number } = {},
): Promise<void> {
  const option = await waitFor(() => {
    const matches = Array.from(document.querySelectorAll('[role="option"]')).filter(
      (o) => o.textContent?.trim() === optionLabel,
    )
    if (matches.length !== 1) {
      throw new Error(`expected exactly one option «${optionLabel}» in the document, found ${matches.length}`)
    }
    return matches[0]
  }, timeout === undefined ? undefined : { timeout })
  fireEvent.click(option)
}
