const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')

const { createServer, runWorker } = require('../server')

const TOKEN = 'a'.repeat(64)

async function fixture(t, execute) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-server-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const server = createServer({ token: TOKEN, allowedRoot: root, execute })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise((resolve) => server.close(resolve)))
  return { root, port: server.address().port }
}

function send(port, body, { token = TOKEN, method = 'POST', pathName = '/upload' } = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request({ host: '127.0.0.1', port, path: pathName, method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` } }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => resolve({ status: response.statusCode,
        headers: response.headers, body: JSON.parse(Buffer.concat(chunks).toString()) }))
    })
    request.on('error', reject)
    request.end(typeof body === 'string' ? body : JSON.stringify(body))
  })
}

test('authenticated request forwards one JSON task and returns bounded structured result', async (t) => {
  const calls = []
  const { port } = await fixture(t, async (input, root) => {
    calls.push({ input, root })
    return { ok: true, code: 'UPLOADED', unsafe: 'hidden' }
  })
  const task = { mode: 'CI_DIRECT', appId: 'wx0123456789abcdef', privateKey: 'private' }
  const result = await send(port, task)
  assert.equal(result.status, 200)
  assert.deepEqual(result.body, { ok: true, code: 'UPLOADED' })
  assert.equal(result.headers['cache-control'], 'no-store')
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].input, task)
})

test('wrong bearer token, route, method, malformed JSON and oversized body cannot invoke SDK', async (t) => {
  let calls = 0
  const { port } = await fixture(t, async () => { calls++; return { ok: true, code: 'UPLOADED' } })
  assert.equal((await send(port, {}, { token: 'wrong' })).status, 401)
  assert.equal((await send(port, {}, { pathName: '/other' })).status, 404)
  assert.equal((await send(port, {}, { method: 'GET' })).status, 405)
  assert.equal((await send(port, '{')).status, 400)
  assert.equal((await send(port, 'x'.repeat(32 * 1024 + 1))).status, 413)
  assert.equal(calls, 0)
})

test('local health endpoint works without the dispatch token and exposes no task state', async (t) => {
  const { port } = await fixture(t, async () => { throw Error('should not run') })
  const result = await send(port, {}, { method: 'GET', pathName: '/healthz', token: 'wrong' })
  assert.equal(result.status, 200)
  assert.deepEqual(result.body, { ok: true, code: 'READY' })
})

test('allows only one SDK task at a time', async (t) => {
  let release
  let entered
  const started = new Promise((resolve) => { entered = resolve })
  const gate = new Promise((resolve) => { release = resolve })
  const { port } = await fixture(t, async () => {
    entered()
    await gate
    return { ok: true, code: 'UPLOADED' }
  })
  const first = send(port, {})
  await started
  const second = await send(port, {})
  assert.equal(second.status, 409)
  assert.deepEqual(second.body, { ok: false, code: 'WORKER_BUSY' })
  release()
  assert.equal((await first).status, 200)
})

test('worker subprocess is killed on deadline and returns unknown outcome', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-child-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const workerPath = path.join(root, 'hang.js')
  fs.writeFileSync(workerPath, 'process.stdin.resume(); setInterval(() => {}, 1000)')
  const started = Date.now()
  const result = await runWorker({ privateKey: 'SECRET' }, root, { workerPath, timeoutMs: 50 })
  assert.deepEqual(result, { ok: false, code: 'UPLOAD_UNKNOWN', failureStage: 'RESPONSE' })
  assert.ok(Date.now() - started < 2000)
})

test('worker subprocess result excludes arbitrary fields, invalid output becomes unknown', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-child-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const workerPath = path.join(root, 'result.js')
  fs.writeFileSync(workerPath, 'process.stdin.resume(); process.stdin.on("end", () => { process.stdout.write(JSON.stringify({ok:false,code:"UPLOAD_FAILED",platformErrorCode:85001,privateKey:"SECRET"})); process.exit(1) })')
  assert.deepEqual(await runWorker({ privateKey: 'SECRET' }, root, { workerPath, timeoutMs: 1000 }),
    { ok: false, code: 'UPLOAD_FAILED', platformErrorCode: 85001 })
  fs.writeFileSync(workerPath, 'process.stdout.write("SECRET"); process.exit(1)')
  assert.deepEqual(await runWorker({ privateKey: 'SECRET' }, root, { workerPath, timeoutMs: 1000 }),
    { ok: false, code: 'UPLOAD_UNKNOWN', failureStage: 'RESPONSE' })
})

test('each worker gets a private writable cwd, HOME and TMPDIR removed after completion', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-isolation-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const tempRoot = path.join(root, 'runtime')
  fs.mkdirSync(tempRoot)
  const marker = path.join(root, 'observed.json')
  const workerPath = path.join(root, 'inspect.js')
  fs.writeFileSync(workerPath, `const fs = require('node:fs'); const path = require('node:path');
    const cwd = process.cwd(); fs.writeFileSync(path.join(cwd, 'compiler-cache'), 'cache');
    fs.writeFileSync(${JSON.stringify(marker)}, JSON.stringify({cwd, home:process.env.HOME,
      tmp:process.env.TMPDIR, mode:fs.statSync(cwd).mode & 0o777}));
    process.stdout.write(JSON.stringify({ok:true,code:'UPLOADED'}));`)
  assert.deepEqual(await runWorker({}, root, { workerPath, tempRoot }), { ok: true, code: 'UPLOADED' })
  const first = JSON.parse(fs.readFileSync(marker, 'utf8'))
  assert.equal(path.dirname(first.cwd), fs.realpathSync(tempRoot))
  assert.equal(first.mode, 0o700)
  assert.equal(first.home, first.cwd)
  assert.equal(first.tmp, first.cwd)
  assert.equal(fs.existsSync(first.cwd), false)
  assert.deepEqual(await runWorker({}, root, { workerPath, tempRoot }), { ok: true, code: 'UPLOADED' })
  assert.notEqual(JSON.parse(fs.readFileSync(marker, 'utf8')).cwd, first.cwd)
  assert.deepEqual(fs.readdirSync(tempRoot), [])
})

test('private worker directory is cleaned after timeout, bad output and spawn failures', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-cleanup-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const tempRoot = path.join(root, 'runtime')
  fs.mkdirSync(tempRoot)
  const workerPath = path.join(root, 'hang.js')
  fs.writeFileSync(workerPath, 'setInterval(() => {}, 1000)')
  await runWorker({}, root, { workerPath, tempRoot, timeoutMs: 50 })
  assert.deepEqual(fs.readdirSync(tempRoot), [])
  fs.writeFileSync(workerPath, 'process.stdout.write("not-json")')
  await runWorker({}, root, { workerPath, tempRoot })
  assert.deepEqual(fs.readdirSync(tempRoot), [])
  const spawnProcess = () => { throw Object.assign(new Error('sensitive'), { code: 'EACCES' }) }
  assert.deepEqual(await runWorker({}, root, { workerPath, tempRoot, spawnProcess }),
    { ok: false, code: 'ENVIRONMENT_FAILED', failureStage: 'ENVIRONMENT', sdkCode: 'EACCES' })
  assert.deepEqual(fs.readdirSync(tempRoot), [])
})

test('only safe structured diagnostics survive the HTTP boundary, including signed platform codes', async (t) => {
  const { port } = await fixture(t, async () => ({ ok: false, code: 'WECHAT_REJECTED',
    failureStage: 'UPLOAD', sdkCode: '20003', platformErrorCode: -10002,
    message: 'PRIVATE KEY and request URL', privateKey: 'secret' }))
  assert.deepEqual((await send(port, {})).body, { ok: false, code: 'WECHAT_REJECTED',
    failureStage: 'UPLOAD', sdkCode: '20003', platformErrorCode: -10002 })
})

test('worker spawn error event is a definite local failure and cleans its directory', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-spawn-error-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const spawnProcess = (_, args, options) => require('node:child_process').spawn(
    path.join(root, 'nonexistent-executable'), args, options,
  )
  assert.deepEqual(await runWorker({}, root, { tempRoot: root, spawnProcess }),
    { ok: false, code: 'ENVIRONMENT_FAILED', failureStage: 'ENVIRONMENT', sdkCode: 'ENOENT' })
  assert.deepEqual(fs.readdirSync(root), [])
})

test('real upload entrypoint rejects invalid input without requiring the SDK', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-invalid-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  assert.deepEqual(await runWorker({}, root, { tempRoot: root }),
    { ok: false, code: 'INPUT_INVALID', failureStage: 'VALIDATION' })
  assert.deepEqual(fs.readdirSync(root), [])
})

test('unknown diagnostic stages and secret-like codes do not cross the HTTP boundary', async (t) => {
  const { port } = await fixture(t, async () => ({ ok: false, code: 'UPLOAD_UNKNOWN',
    failureStage: 'https://secret', sdkCode: 'PRIVATE KEY\nsecret', platformErrorCode: 0 }))
  assert.deepEqual((await send(port, {})).body, { ok: false, code: 'UPLOAD_UNKNOWN' })
})

test('an error after the spawn event stays unknown and cannot be retried as a local failure', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-post-spawn-error-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const { EventEmitter } = require('node:events')
  const { PassThrough } = require('node:stream')
  const spawnProcess = () => {
    const child = new EventEmitter()
    child.stdin = new PassThrough()
    child.stdout = new PassThrough()
    queueMicrotask(() => {
      child.emit('spawn')
      child.emit('error', Object.assign(new Error('sensitive kill failure'), { code: 'EPERM' }))
      child.emit('close', 1)
    })
    return child
  }
  assert.deepEqual(await runWorker({}, root, { tempRoot: root, spawnProcess }),
    { ok: false, code: 'UPLOAD_UNKNOWN', failureStage: 'RESPONSE', sdkCode: 'EPERM' })
  assert.deepEqual(fs.readdirSync(root), [])
})
