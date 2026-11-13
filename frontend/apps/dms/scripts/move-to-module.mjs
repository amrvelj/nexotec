#!/usr/bin/env node
// Moves a module's files into src/modules/<module>/ from a mapping file and
// rewrites every relative import that names them (KAN-636, ADR-158).
//
//   node scripts/move-to-module.mjs scripts/module-moves/<module>.json [--dry-run]
//
// Run from frontend/apps/dms. The mapping's `moves` is a list of [from, to]
// pairs relative to that folder. Each pair is applied with `git mv`; then every
// .ts/.tsx file under src/ gets its relative specifiers rewritten — a specifier
// that named a moved file names it at its new path, and a moved file's own
// specifiers are recomputed from its new folder. The specifiers it reads are
// `from '…'` (imports and re-exports), `import '…'`, `import('…')` and Vitest's
// `vi.mock` / `vi.doMock` / `vi.unmock` / `vi.importActual` / `vi.importMock`.
// Nothing else in a file changes, so a move PR is reviewed from its mapping.
//
// What it does not do (by hand, in the same PR): point another module's
// production imports at the module's index.ts, and update a test that names a
// file by path in a string (the architecture tests' allow-lists).

import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, posix, relative, resolve, sep } from 'node:path'

const APP_ROOT = resolve(dirname(new URL(import.meta.url).pathname), '..')
const SRC = join(APP_ROOT, 'src')
const SOURCE_EXT = /\.(ts|tsx)$/
const RESOLVE_SUFFIXES = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']
const SPECIFIER =
  /(\bfrom\s+|\bimport\s*\(\s*|\bimport\s+|\bvi\.(?:mock|doMock|unmock|importActual|importMock)\s*\(\s*)(['"])(\.\.?\/[^'"\n]*)\2/g

const toPosix = (path) => path.split(sep).join('/')
const rel = (path) => toPosix(relative(APP_ROOT, path))

function allFiles(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) allFiles(full, out)
    else out.push(rel(full))
  }
  return out
}

/** The app-relative file a specifier names from `importer`, as the tree is before the move. */
function resolveSpecifier(importer, specifier, existing) {
  const base = posix.normalize(posix.join(posix.dirname(importer), specifier))
  for (const suffix of RESOLVE_SUFFIXES) {
    if (existing.has(base + suffix)) return { target: base + suffix, suffix }
  }
  return null
}

/** `to` as a specifier written from `fromFile`, with the suffix the original left implicit dropped again. */
function specifierBetween(fromFile, to, suffix) {
  const target = suffix ? to.slice(0, to.length - suffix.length) : to
  let spec = posix.relative(posix.dirname(fromFile), target)
  if (!spec.startsWith('.')) spec = './' + spec
  return spec
}

export function planRewrites(files, moves, read) {
  const moved = new Map(moves)
  const existing = new Set(files)
  const newPathOf = (file) => moved.get(file) ?? file
  const rewrites = []
  for (const file of files.filter((f) => SOURCE_EXT.test(f))) {
    const source = read(file)
    const next = source.replace(SPECIFIER, (whole, lead, quote, specifier) => {
      const found = resolveSpecifier(file, specifier, existing)
      if (!found) {
        if (moved.has(file)) throw new Error(`${file}: cannot resolve '${specifier}' to recompute it after the move`)
        return whole
      }
      if (!moved.has(file) && !moved.has(found.target)) return whole
      const spec = specifierBetween(newPathOf(file), newPathOf(found.target), found.suffix)
      return `${lead}${quote}${spec}${quote}`
    })
    if (next !== source) rewrites.push({ file: newPathOf(file), source: next })
  }
  return rewrites
}

function main(argv) {
  const [mappingPath, ...flags] = argv
  if (!mappingPath) throw new Error('usage: node scripts/move-to-module.mjs scripts/module-moves/<module>.json [--dry-run]')
  const dryRun = flags.includes('--dry-run')
  const { moves } = JSON.parse(readFileSync(resolve(APP_ROOT, mappingPath), 'utf-8'))
  for (const [from, to] of moves) {
    if (!existsSync(join(APP_ROOT, from))) throw new Error(`${from} does not exist`)
    if (existsSync(join(APP_ROOT, to))) throw new Error(`${to} already exists`)
  }
  const files = allFiles(SRC)
  const rewrites = planRewrites(files, moves, (file) => readFileSync(join(APP_ROOT, file), 'utf-8'))
  for (const [from, to] of moves) console.log(`${dryRun ? 'would move' : 'move'}  ${from} -> ${to}`)
  for (const { file } of rewrites) console.log(`${dryRun ? 'would rewrite' : 'rewrite'}  ${file}`)
  if (dryRun) return
  for (const [from, to] of moves) {
    execFileSync('mkdir', ['-p', dirname(join(APP_ROOT, to))])
    execFileSync('git', ['mv', from, to], { cwd: APP_ROOT })
  }
  for (const { file, source } of rewrites) writeFileSync(join(APP_ROOT, file), source)
}

if (import.meta.url === `file://${process.argv[1]}`) main(process.argv.slice(2))
