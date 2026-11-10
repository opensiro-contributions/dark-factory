import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

// Exercise the actual shipped feature-core helper without starting workflow agents.
const source = readFileSync(new URL('../plugins/dark-factory/workflows/feature-core.js', import.meta.url), 'utf8')
const implementation = source.match(/function pairwiseDisjoint\(sets\) \{[\s\S]*?\n\}/)?.[0]
assert.ok(implementation, 'first-party file-collision guard must be present')
const pairwiseDisjoint = runInNewContext(`${implementation}\npairwiseDisjoint`)

test('distinct known file paths may run in parallel', () => {
  assert.equal(pairwiseDisjoint([['app/src/a.py'], ['app/src/b.py']]), true)
})

test('exactly conflicting file paths must be serialized', () => {
  assert.equal(pairwiseDisjoint([['app/src/a.py'], ['app/src/a.py']]), false)
})

test('missing or empty file scope must fail closed to serial execution', () => {
  assert.equal(pairwiseDisjoint([['app/src/a.py'], []]), false)
  assert.equal(pairwiseDisjoint([['app/src/a.py'], null]), false)
})

test('lexical aliases cannot evade overlapping file admission', () => {
  assert.equal(pairwiseDisjoint([['app/src/./a.py'], ['app/src/a.py']]), false)
  assert.equal(pairwiseDisjoint([['app/src/sub/../a.py'], ['app/src/a.py']]), false)
  assert.equal(pairwiseDisjoint([['app\\src\\a.py'], ['app/src/a.py']]), false)
})

test('paths outside the declared repository-relative scope fail closed', () => {
  assert.equal(pairwiseDisjoint([['../src/a.py'], ['app/src/b.py']]), false)
  assert.equal(pairwiseDisjoint([['/tmp/a.py'], ['app/src/b.py']]), false)
})
