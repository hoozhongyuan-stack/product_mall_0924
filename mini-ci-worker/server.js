const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const os = require('node:os')
const { safeDiagnostics, failure } = require('./upload')
const { spawn } = require('node:child_process')

const MAX_REQUEST_BYTES = 32 * 1024
const MAX_RESPONSE_BYTES = 4 * 1024
const WORKER_TIMEOUT_MS = 170 * 1000

function normalizeResult(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || typeof value.ok !== 'boolean' || typeof value.code !== 'string'
    || !/^[A-Z][A-Z0-9_]{0,63}$/.test(value.code)) {
    return { ok: false, code: 'UPLOAD_UNKNOWN' }
  }
  return { ok: value.ok, code: value.code, ...(!value.ok ? safeDiagnostics(value) : {}) }
}

function killWorker(child) {
  if (!child.pid) return
  try {
    if (process.platform === 'win32') child.kill('SIGKILL')
    else process.kill(-child.pid, 'SIGKILL')
  } catch {
    try { child.kill('SIGKILL') } catch { /* already stopped */ }
  }
}

function runWorker(input, allowedRoot, { workerPath = path.join(__dirname, 'upload.js'),
  timeoutMs = WORKER_TIMEOUT_MS, tempRoot = os.tmpdir(), spawnProcess = spawn } = {}) {
  return new Promise((resolve) => {
    let child
    let workDirectory
    let timer
    let finished = false
    let timedOut = false
    let spawned = false
    let processError
    let output = Buffer.alloc(0)
    const finish = (result) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      try {
        if (workDirectory) fs.rmSync(workDirectory, { recursive: true, force: true, maxRetries: 2 })
        resolve(result)
      } catch (error) {
        // Cleanup failure must not turn a possibly completed upload into a retryable failure.
        resolve(failure('UPLOAD_UNKNOWN', 'ENVIRONMENT', error))
      }
    }
    try {
      workDirectory = fs.mkdtempSync(path.join(fs.realpathSync(tempRoot), 'mini-ci-task-'))
      fs.chmodSync(workDirectory, 0o700)
      child = spawnProcess(process.execPath, [workerPath], {
        cwd: workDirectory,
        stdio: ['pipe', 'pipe', 'ignore'],
        detached: process.platform !== 'win32',
        env: { PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
          NODE_ENV: 'production', MINI_CI_ALLOWED_ROOT: allowedRoot,
          HOME: workDirectory, TMPDIR: workDirectory, TMP: workDirectory, TEMP: workDirectory },
      })
    } catch (error) {
      finish(failure('ENVIRONMENT_FAILED', 'ENVIRONMENT', error))
      return
    }
    timer = setTimeout(() => {
      timedOut = true
      killWorker(child)
    }, timeoutMs)
    child.on('spawn', () => { spawned = true })
    child.stdin.on('error', () => {})
    child.stdout.on('data', (chunk) => {
      if (output.length + chunk.length > MAX_RESPONSE_BYTES) {
        timedOut = true
        killWorker(child)
        return
      }
      output = Buffer.concat([output, chunk])
    })
    child.on('error', (error) => {
      if (finished || processError) return
      // Errors can also follow a successful spawn (for example a failed kill).
      const executionStarted = spawned || Number.isInteger(child.pid) || timedOut
      processError = executionStarted
        ? failure('UPLOAD_UNKNOWN', 'RESPONSE', error)
        : failure('ENVIRONMENT_FAILED', 'ENVIRONMENT', error)
      killWorker(child)
      finish(processError)
    })
    child.on('close', (code) => {
      killWorker(child)
      if (processError) return finish(processError)
      if (timedOut) return finish(failure('UPLOAD_UNKNOWN', 'RESPONSE'))
      try {
        const parsed = JSON.parse(output.toString('utf8'))
        const result = normalizeResult(parsed)
        if ((result.ok && code !== 0) || (!result.ok && code === 0)) {
          return finish(failure('UPLOAD_UNKNOWN', 'RESPONSE'))
        }
        finish(result)
      } catch {
        finish(failure('UPLOAD_UNKNOWN', 'RESPONSE'))
      }
    })
    child.stdin.end(JSON.stringify(input))
  })
}

function authorized(header, token) {
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) return false
  const candidate = crypto.createHash('sha256').update(header.slice(7)).digest()
  const expected = crypto.createHash('sha256').update(token).digest()
  return crypto.timingSafeEqual(candidate, expected)
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    let tooLarge = false
    request.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_REQUEST_BYTES) {
        tooLarge = true
        chunks.length = 0
      } else if (!tooLarge) chunks.push(chunk)
    })
    request.on('end', () => {
      if (tooLarge) return reject({ status: 413, code: 'INPUT_TOO_LARGE' })
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))) }
      catch { reject({ status: 400, code: 'INPUT_INVALID' }) }
    })
    request.on('error', () => reject({ status: 400, code: 'INPUT_INVALID' }))
  })
}

function send(response, status, result) {
  if (response.destroyed || response.headersSent) return
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' })
  response.end(`${JSON.stringify(result)}\n`)
}

function createServer({ token, allowedRoot, execute = runWorker } = {}) {
  if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) {
    throw new Error('invalid dispatch token')
  }
  if (typeof allowedRoot !== 'string' || !path.isAbsolute(allowedRoot)) {
    throw new Error('invalid allowed root')
  }
  let busy = false
  const server = http.createServer(async (request, response) => {
    if (request.url === '/healthz' && request.method === 'GET') {
      return send(response, 200, { ok: true, code: 'READY' })
    }
    if (request.url !== '/upload') return send(response, 404, { ok: false, code: 'NOT_FOUND' })
    if (request.method !== 'POST') return send(response, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' })
    if (!authorized(request.headers.authorization, token)) {
      return send(response, 401, { ok: false, code: 'UNAUTHORIZED' })
    }
    if (busy) return send(response, 409, { ok: false, code: 'WORKER_BUSY' })
    busy = true
    try {
      if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] || '')) {
        return send(response, 415, { ok: false, code: 'CONTENT_TYPE_INVALID' })
      }
      if (Number(request.headers['content-length']) > MAX_REQUEST_BYTES) {
        return send(response, 413, { ok: false, code: 'INPUT_TOO_LARGE' })
      }
      const input = await readBody(request)
      const result = normalizeResult(await execute(input, allowedRoot))
      send(response, 200, result)
    } catch (error) {
      if (error && Number.isInteger(error.status) && error.code) {
        send(response, error.status, { ok: false, code: error.code })
      } else send(response, 500, { ok: false, code: 'UPLOAD_UNKNOWN' })
    } finally {
      busy = false
    }
  })
  server.requestTimeout = 10_000
  server.headersTimeout = 5_000
  server.keepAliveTimeout = 1_000
  return server
}

function main() {
  const tokenFile = process.env.MINI_CI_DISPATCH_TOKEN_FILE
  if (!tokenFile) throw new Error('dispatch token file is required')
  const token = fs.readFileSync(tokenFile, 'utf8').trim()
  const server = createServer({ token, allowedRoot: process.env.MINI_CI_ALLOWED_ROOT })
  server.listen(8787, '0.0.0.0')
}

if (require.main === module) main()

module.exports = { createServer, runWorker }
