import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// KAN-850 (Anto, 2026-11-26): an order's job is «Gruppe» on every screen, as the order page
// already calls it — never «Arbeit». «Arbeit» stays the word for labour (a labour position, a
// work booking, Arbeitszeit, Arbeitskategorie); only the job is renamed. Each string below names a
// job, in the four languages; a new string that names one joins the list.

const LOCALES = join(dirname(fileURLToPath(import.meta.url)), 'locales')

const NAMES_A_JOB: Array<[bundle: string, key: string]> = [
  ['apiRefusal.reasons.aftersales', 'service_order_clock_has_run'],
  ['apiRefusal.reasons.aftersales', 'service_order_job_accepted'],
  ['apiRefusal.reasons.aftersales', 'service_order_job_already_answered'],
  ['apiRefusal.reasons.aftersales', 'service_order_job_name_required'],
  ['apiRefusal.reasons.aftersales', 'service_order_job_time_clocked'],
  ['apiRefusal.reasons.planning', 'planning_person_twice_on_job'],
  ['apiRefusal.reasons.planning', 'planning_work_needs_job_and_vehicle'],
  ['planner', 'booking.job'],
  ['plannerResources', 'entryDialog.mandatoryHint'],
  ['serviceCatalogue', 'subtitle'],
  ['serviceCatalogue', 'dialog.activeHint'],
]

// The word for a job, and the old one it replaces (whole words, any case).
const WORDS: Record<string, { job: RegExp; old: RegExp }> = {
  de: { job: /\bGruppen?\b/, old: /\bArbeit(en)?\b/ },
  en: { job: /\bgroups?\b/i, old: /\bjobs?\b/i },
  fr: { job: /\bgroupes?\b/i, old: /\btravail\b|\btravaux\b|\btâche\b/i },
  it: { job: /\bgrupp[oi]\b/i, old: /\blavor[oi]\b|\bintervento\b/i },
}

function text(lang: string, bundle: string, key: string): string {
  let node: unknown = JSON.parse(readFileSync(join(LOCALES, lang, `${bundle}.json`), 'utf8'))
  for (const part of key.split('.')) node = (node as Record<string, unknown>)[part]
  if (typeof node !== 'string') throw new Error(`${lang}/${bundle}.json has no string at ${key}`)
  return node
}

// Where «travail» / «lavoro» means labour, not the job, it stays: the work booking («Le travail est
// planifié…», «Il lavoro è pianificato…») and a labour position, time or category («positions de
// travail», «posizioni di lavoro»).
const LABOUR: Record<string, RegExp> = {
  fr: /^Le travail |\b(positions?|temps|catégories?) de travail\b/g,
  it: /^Il lavoro |\b(posizion[ei]|tempo|categori[ae]) di lavoro\b/g,
}

describe('an order’s job is «Gruppe» on screen (KAN-850)', () => {
  for (const lang of Object.keys(WORDS)) {
    for (const [bundle, key] of NAMES_A_JOB) {
      it(`${lang}: ${bundle} ${key}`, () => {
        const value = text(lang, bundle, key)
        expect(value).toMatch(WORDS[lang].job)
        expect(value.replace(LABOUR[lang] ?? /^$/, '')).not.toMatch(WORDS[lang].old)
      })
    }
  }
})
