/**
 * Static conformance check for the DSH plugin in `dsh_plugin/ai-embedded-bridge`.
 *
 * It reproduces the parts of awesome-dsh-plugin's gate that can be checked
 * locally, without network and without GitHub:
 *
 *   scripts/check-submission.mjs -> hasBundle() / scanTree()
 *   scripts/lib/entries.mjs      -> slugFor() / validateEntries()
 *
 * It proves the SUBMISSION SHAPE — manifest, patch layer, entry file — and
 * nothing more. It cannot prove that the plugin mounts, because that needs the
 * real Loader and an installed profile. Run it after changing package.json,
 * cordis.patch.yml or the entry file.
 *
 *   node scripts/check-dsh-plugin-submission.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const PLUGIN = path.join(ROOT, 'dsh_plugin/ai-embedded-bridge')
const ENTRY_DIR = path.join(ROOT, 'docs/awesome-dsh-plugin-submission')

const require = createRequire(import.meta.url)

/** js-yaml is not a dependency of this repo; borrow it from the running dsh install. */
function loadYaml() {
  try {
    return require('js-yaml')
  } catch {
    /* not installed here — look next to the running harness */
  }
  const globalRoot = path.join(process.env.APPDATA ?? '', 'npm/node_modules/@deepseek-ai/dsh/node_modules')
  try {
    return require(path.join(globalRoot, 'js-yaml'))
  } catch {
    return null
  }
}

const yaml = loadYaml()
const problems = []
const ok = []
const check = (cond, good, bad) => (cond ? ok.push(good) : problems.push(bad))

// --- 1. enumerate every package.json in the tree, like scanTree() does -------
const pkgs = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === '.git' || e.name === 'node_modules') continue
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p)
    else if (e.name === 'package.json') pkgs.push(path.relative(ROOT, p).replaceAll('\\', '/'))
  }
})(ROOT)
ok.push(`package.json in tree (gate reads the first 40): ${pkgs.join(', ')}`)
check(pkgs.length <= 40, `${pkgs.length} manifest(s), under the gate's cap of 40`, `more than 40 manifests — the gate would report "not checked"`)

// --- 2. the gate's hasBundle(): the entry's subpath manifest must declare it -
const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN, 'package.json'), 'utf8'))
check(!!manifest.dsh?.bundle, 'dsh.bundle is declared', 'package.json has no dsh.bundle — the gate rejects here')
const patchRef = manifest.dsh?.bundle?.patch
check(typeof patchRef === 'string' && patchRef.length > 0, `dsh.bundle.patch = ${patchRef}`, 'dsh.bundle.patch is missing')
const patchPath = path.join(PLUGIN, patchRef ?? '')
check(!!patchRef && fs.existsSync(patchPath), 'the patch file ships next to package.json', `patch file not found: ${patchRef}`)
check(
  (manifest.files ?? []).some((f) => f.includes('cordis.patch')),
  'cordis.patch.yml is in "files" (pnpm pack must not drop it)',
  'cordis.patch.yml is not listed in "files" — a packed install would lose the layer',
)
check(!!manifest.exports?.['.'], 'exports["."] present, so the patch row name self-resolves', 'no exports["."] — the patch row name would not resolve')
const mainEntry = path.join(PLUGIN, manifest.main ?? '')
check(!!manifest.main && fs.existsSync(mainEntry), `entry file present: ${manifest.main}`, `main entry missing: ${manifest.main}`)

// --- 3. official @deepseek-ai/* are peers, never dependencies ---------------
const official = (o) => Object.keys(o ?? {}).filter((k) => k.startsWith('@deepseek-ai/'))
check(!official(manifest.dependencies).length, 'no @deepseek-ai/* in dependencies', `@deepseek-ai/* in dependencies: ${official(manifest.dependencies).join(', ')}`)
const peers = new Set(official(manifest.peerDependencies))
const src = fs.readFileSync(mainEntry, 'utf8')
const imported = [...src.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]).filter((s) => s.startsWith('@deepseek-ai/'))
for (const p of imported) check(peers.has(p), `imported ${p} is declared as a peer`, `imported ${p} is NOT declared as a peer`)
ok.push(`peerDependencies: ${[...peers].join(', ') || '(none)'}`)

// --- 4. the peer ranges must admit the harness versions actually in use -----
function loadSemver() {
  try {
    return require('semver')
  } catch {
    /* same borrow-the-harness's-copy trick as js-yaml */
  }
  try {
    return require(path.join(process.env.APPDATA ?? '', 'npm/node_modules/@deepseek-ai/dsh/node_modules/semver'))
  } catch {
    return null
  }
}
const semver = loadSemver()
if (semver) {
  // The harness copy the dsh CLI ships is what a profile resolves the peers
  // to. A range that misses it makes pnpm/npm install a SECOND copy of the
  // runtime, and the plugin then talks to a different module instance than
  // the host — which is exactly the failure this whole declaration prevents.
  const harness = path.join(process.env.APPDATA ?? '', 'npm/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai')
  const probe = []
  for (const name of ['@deepseek-ai/dsh-llm', '@deepseek-ai/dsh-session-title']) {
    const manifestPath = path.join(harness, name.replace('@deepseek-ai/', ''), 'package.json')
    if (fs.existsSync(manifestPath)) probe.push([name, JSON.parse(fs.readFileSync(manifestPath, 'utf8')).version])
  }
  if (!probe.length) ok.push('no installed harness found — peer-range satisfiability not checked')
  for (const [name, version] of probe) {
    const range = manifest.peerDependencies?.[name]
    check(
      !!range && semver.satisfies(version, range),
      `${name}: installed ${version} satisfies the declared range`,
      `${name}: installed ${version} does NOT satisfy "${range}" — pnpm/npm would pull a SECOND copy of the runtime`,
    )
  }
} else {
  ok.push('semver unavailable — peer-range satisfiability not checked')
}

// --- 5. the patch layer parses and follows the row schema ------------------
if (yaml && fs.existsSync(patchPath)) {
  const doc = yaml.load(fs.readFileSync(patchPath, 'utf8'))
  check(Array.isArray(doc), 'cordis.patch.yml parses to a patch array', 'cordis.patch.yml is not a YAML array of patch ops')
  const rows = Array.isArray(doc) ? (doc[0]?.insert ?? []) : []
  check(rows.length > 0, `patch inserts ${rows.length} row(s)`, 'patch inserts no rows')
  for (const row of rows) {
    check(typeof row.id === 'string' && row.id, `row id "${row.id}"`, 'a row has no id')
    check(typeof row.name === 'string' && row.name, `row name "${row.name}"`, 'a row has no name')
    check(!String(row.name).startsWith('.'), `row name "${row.name}" is a bare specifier`, `row name "${row.name}" is relative — resolution is from the package dir, use the package name`)
  }
} else {
  ok.push('js-yaml unavailable — cordis.patch.yml not parsed')
}

// --- 6. the submission entry, against entries.mjs' own rules ---------------
const slugFor = (url) => {
  const p = url.replace(/^https:\/\/github\.com\//, '').replace(/\/+$/, '')
  const repo = p.split('/').slice(0, 2).join('/')
  const sub = p.includes('/tree/') ? p.split('/tree/')[1].replace(/^[^/]+\//, '') : null
  return sub ? `${repo.replaceAll('/', '__')}--${sub.replaceAll('/', '-')}` : repo.replaceAll('/', '__')
}
const CAT_IDS = ['agi', 'ui', 'usage', 'theme', 'model', 'identity', 'session', 'memory', 'tools', 'wsl', 'browser', 'vision', 'voice', 'docs', 'skill', 'workflow', 'git', 'notify', 'dev', 'security', 'remote', 'market', 'fun']
const ENTRY_KEYS = new Set(['url', 'name', 'category', 'description', 'tarball'])
const files = fs.existsSync(ENTRY_DIR) ? fs.readdirSync(ENTRY_DIR).filter((f) => f.endsWith('.yml')) : []
check(files.length > 0, `${files.length} entry file(s)`, `no entry file in ${path.relative(ROOT, ENTRY_DIR)}`)
for (const f of files) {
  if (!yaml) {
    ok.push(`${f}: not parsed (js-yaml unavailable)`)
    continue
  }
  const doc = yaml.load(fs.readFileSync(path.join(ENTRY_DIR, f), 'utf8'))
  const extra = Object.keys(doc).filter((k) => !ENTRY_KEYS.has(k))
  check(!extra.length, `${f}: only allowed keys`, `${f}: unknown field(s): ${extra.join(', ')}`)
  check(path.basename(f, '.yml') === slugFor(doc.url), `${f}: filename matches slugFor(url)`, `${f}: filename must be "${slugFor(doc.url)}.yml"`)
  check(CAT_IDS.includes(doc.category), `${f}: category "${doc.category}" is valid`, `${f}: invalid category "${doc.category}"`)
  check(typeof doc.description?.en === 'string' && doc.description.en.trim() !== '', `${f}: description.en present`, `${f}: description.en is required`)
  check(typeof doc.description?.en === 'string' && !doc.description.en.includes('\n'), `${f}: description.en is a single line`, `${f}: description.en must be a single line`)
  check(!!doc.name?.trim(), `${f}: name present`, `${f}: name is required`)
  check(!doc.tarball, `${f}: no tarball`, `${f}: tarball set, but no GitHub release asset exists for it`)
  ok.push(`${f}: ${doc.category} -> ${doc.url}`)
}

for (const o of ok) console.log(`  ok  ${o}`)
for (const p of problems) console.error(`  !!  ${p}`)
console.log(`\n${problems.length ? `${problems.length} problem(s)` : 'all static checks pass'} (${ok.length} assertions)`)
console.log('Reminder: this proves shape only. The real gate also reads your source and checks every claim in the entry description.')
process.exit(problems.length ? 1 : 0)
