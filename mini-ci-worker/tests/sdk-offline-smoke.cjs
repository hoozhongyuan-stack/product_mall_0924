// Use the README Docker command. This never calls ci.upload and mounts no real credentials.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { runWorker } = require('../server')
const tempRoot = fs.mkdtempSync('/tmp/mini-ci-offline-')
runWorker({}, path.join(__dirname, 'fixtures'), {
  workerPath: path.join(__dirname, 'sdk-offline-worker.cjs'), tempRoot, timeoutMs: 45000,
}).then((result) => {
  assert.deepEqual(result, { ok: true, code: 'COMPILED_OFFLINE' })
  assert.deepEqual(fs.readdirSync(tempRoot), [])
  process.stdout.write('Official SDK compiled fixture offline with read-only source; private workspace cleaned.\n')
}).finally(() => fs.rmSync(tempRoot, { recursive: true, force: true }))
