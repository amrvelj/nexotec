import type { ComponentType } from 'react'
import { AiPromptSeedsPanel } from './AiPromptSeedsPanel'
import { StandardTextSeedsPanel } from './StandardTextSeedsPanel'
import { UiStringsPanel } from './UiStringsPanel'

/**
 * KAN-209 — the sources the one Translations panel edits (D-A-23: "one panel,
 * three tables"): the UI strings (`ui_translation`) and the standard-text
 * seed library (`standard_text`, source `platform_seed`, KAN-208) and the AI
 * prompt seeds (`integration_ai_prompt`, KAN-227) — each a panel of its own
 * reusing `FourLanguageEditor` for the four-column edit and the diff against
 * the seed, with a `labelKey` in all four bundles. The page renders one tab per entry, in this order, and keeps the
 * chosen one in the URL (`?source=<id>`); a panel owns the rest of the URL's
 * state, so a source's own search parameters never meet another's.
 */
export interface TranslationSource {
  /** The `?source=` value — stable, it is in shared links. */
  id: string
  /** The tab's label, a bundle key. */
  labelKey: string
  Panel: ComponentType
}

export const TRANSLATION_SOURCES: readonly TranslationSource[] = [
  { id: 'ui', labelKey: 'translations.sources.ui', Panel: UiStringsPanel },
  { id: 'standardTexts', labelKey: 'translations.sources.standardTexts', Panel: StandardTextSeedsPanel },
  { id: 'aiPrompts', labelKey: 'translations.sources.aiPrompts', Panel: AiPromptSeedsPanel },
]
