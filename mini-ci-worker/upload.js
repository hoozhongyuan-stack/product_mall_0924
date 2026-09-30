const fs = require('node:fs')
const path = require('node:path')

const MAX_INPUT_BYTES = 32 * 1024
const MAX_CONFIG_BYTES = 64 * 1024
const MAX_PROJECT_ENTRIES = 20000
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000

function validInput(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && (value.mode === 'CI_DIRECT' || value.mode === 'DIRECT_COMMIT')
    && /^wx[0-9a-fA-F]{16}$/.test(value.appId)
    && (value.mode === 'CI_DIRECT'
      ? value.targetAppId === undefined
      : /^wx[0-9a-fA-F]{16}$/.test(value.targetAppId))
    && typeof value.projectPath === 'string' && path.isAbsolute(value.projectPath)
    && typeof value.privateKey === 'string' && value.privateKey.length <= 16 * 1024
    && /^-----BEGIN (?:RSA )?PRIVATE KEY-----/.test(value.privateKey.trim())
    && /-----END (?:RSA )?PRIVATE KEY-----$/.test(value.privateKey.trim())
    && typeof value.version === 'string' && /^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/.test(value.version)
    && typeof value.description === 'string' && value.description.length <= 500
    && (value.robot === undefined || (Number.isInteger(value.robot) && value.robot >= 1 && value.robot <= 30))
}

function projectCheck(projectPath, allowedRoot, appId, mode, targetAppId) {
  try {
    const root = fs.realpathSync(allowedRoot)
    const project = fs.realpathSync(projectPath)
    if (project !== root && !project.startsWith(`${root}${path.sep}`)) {
      return { code: 'PROJECT_INVALID' }
    }
    if (!fs.statSync(project).isDirectory()) return { code: 'PROJECT_INVALID' }
    const pending = [project]
    let entryCount = 0
    while (pending.length) {
      const directory = pending.pop()
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        entryCount += 1
        if (entryCount > MAX_PROJECT_ENTRIES || entry.isSymbolicLink()
          || (!entry.isDirectory() && !entry.isFile())) return { code: 'PROJECT_INVALID' }
        if (entry.isDirectory()) pending.push(path.join(directory, entry.name))
      }
    }
    const configPath = path.join(project, 'project.config.json')
    const configStat = fs.lstatSync(configPath)
    if (!configStat.isFile() || configStat.size > MAX_CONFIG_BYTES) {
      return { code: 'PROJECT_INVALID' }
    }
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    if (config.appid !== appId) return { code: 'PROJECT_APPID_MISMATCH' }
    const extPath = path.join(project, 'ext.json')
    let extStat
    try { extStat = fs.lstatSync(extPath) }
    catch (error) {
      if (error.code !== 'ENOENT') return { code: 'TARGET_CONFIG_INVALID' }
    }
    if (mode === 'CI_DIRECT') {
      if (extStat) return { code: 'TARGET_CONFIG_INVALID' }
    } else {
      if (!extStat || !extStat.isFile() || extStat.size > MAX_CONFIG_BYTES) {
        return { code: 'TARGET_CONFIG_INVALID' }
      }
      let ext
      try { ext = JSON.parse(fs.readFileSync(extPath, 'utf8')) }
      catch { return { code: 'TARGET_CONFIG_INVALID' } }
      if (ext.extEnable !== true || ext.directCommit !== true || ext.extAppid !== targetAppId) {
        return { code: 'TARGET_CONFIG_INVALID' }
      }
    }
    return { project }
  } catch {
    return { code: 'PROJECT_INVALID' }
  }
}

const FAILURE_STAGES = new Set(['ENVIRONMENT', 'VALIDATION', 'COMPILE', 'UPLOAD', 'RESPONSE'])
const PLATFORM_REASONS = new Set(['IP_NOT_ALLOWED', 'SIGNATURE_INVALID', 'PACKAGE_TOO_LARGE',
  'FILE_MISSING', 'INNER_UPLOAD_FAILED', 'TICKET_REQUEST_FAILED'])
const ENVIRONMENT_CODES = new Set(['EACCES', 'EPERM', 'EROFS', 'ENOSPC', 'EMFILE', 'ENFILE', 'ENOMEM'])
// Official 2.1.47 config/config.js: only codes which occur before code submission.
const COMPILE_CODES = new Set([10005, 10006, 10007, 10008, 10009, 10031, 10032, 10033,
  10034, 10035, 10036, 10037, 10038, 10045, 10046, 10081, 10091, 10092, 10093, 20000])

function safeSdkCode(value) {
  const code = Number.isInteger(value) ? String(value) : value
  return typeof code === 'string' && /^[A-Za-z0-9_+.-]{1,48}$/.test(code) ? code : undefined
}

function safePlatformCode(value) {
  return Number.isInteger(value) && value !== 0 && Math.abs(value) <= 99999999 ? value : undefined
}

function safeDiagnostics(value) {
  if (!value || typeof value !== 'object') return {}
  const sdkCode = safeSdkCode(value.sdkCode)
  const platformErrorCode = safePlatformCode(value.platformErrorCode)
  const innerPlatformErrorCode = safePlatformCode(value.innerPlatformErrorCode)
  return {
    ...(FAILURE_STAGES.has(value.failureStage) ? { failureStage: value.failureStage } : {}),
    ...(sdkCode === undefined ? {} : { sdkCode }),
    ...(platformErrorCode === undefined ? {} : { platformErrorCode }),
    ...(innerPlatformErrorCode === undefined ? {} : { innerPlatformErrorCode }),
    ...(PLATFORM_REASONS.has(value.platformReason) ? { platformReason: value.platformReason } : {}),
  }
}

function platformEnvelope(error) {
  const direct = safePlatformCode(error && error.errCode)
  if (direct !== undefined && error.errorStage !== 'builder') {
    return { errCode: direct, errMsg: error.errMsg }
  }
  // 2.1.47 wraps backend JSON and signature-CGI failures in CodeError.message.
  // Read only recognized complete envelopes; never return the source text.
  if (!error || ![20001, 20003].includes(error.code)
    || typeof error.message !== 'string' || error.message.length > 16 * 1024) return undefined
  const body = error.message.replace(/^(?:Error: ){0,3}/, '')
  if (body.startsWith('{') && body.endsWith('}')) {
    try {
      const envelope = JSON.parse(body)
      const errCode = safePlatformCode(envelope.errCode)
      return errCode === undefined ? undefined : { errCode, errMsg: envelope.errMsg }
    } catch { return undefined }
  }
  const match = /^errCode: (-?\d{1,8}); errMsg: ([\s\S]*)$/.exec(body)
  if (!match) return undefined
  const errCode = safePlatformCode(Number(match[1]))
  return errCode === undefined ? undefined : { errCode, errMsg: match[2] }
}

function knownPlatformReason(message) {
  if (/^(?:invalid ip|ip (?:[\da-f.:]+ )?(?:is )?not in (?:white ?list))\b/i.test(message)) {
    return 'IP_NOT_ALLOWED'
  }
  if (/^(?:invalid (?:private key|upload key|signature)|(?:private key|upload key|signature) (?:is )?(?:invalid|not valid|mismatch)|verify signature fail(?:ed)?|signature verification fail(?:ed)?)\b/i.test(message)) {
    return 'SIGNATURE_INVALID'
  }
  if (/^(?:(?:code )?package (?:size )?(?:is )?(?:too large|exceeds? (?:the )?(?:size )?limit)|(?:code )?package size limit exceeded)\b/i.test(message)
    || /^(?:main package )?source size \d{1,10}(?:\.\d{1,3})?(?:KB|MB) exceed max limit \d{1,10}(?:\.\d{1,3})?(?:KB|MB)$/i.test(message)) {
    return 'PACKAGE_TOO_LARGE'
  }
  if (/^(?:file (?:[A-Za-z0-9_./-]+ )?(?:not found|is missing)|missing file)\b/i.test(message)
    || /^error: iconPath=[A-Za-z0-9_./-]{1,512}, file not found$/i.test(message)) {
    return 'FILE_MISSING'
  }
  return undefined
}

function platformMessageDiagnostics(value, depth = 0) {
  if (depth >= 3 || typeof value !== 'string' || value.length > 16 * 1024) return {}
  const message = value.trim()
  const upload = /^inner upload fail with errcode:\s*(-?\d{1,8}),\s*errmsg:\s*([\s\S]*)$/i.exec(message)
  const ticket = /^get new ticket fail:?\s+innerCode:\s*(-?\d{1,8})(?:[,;]?\s+(?:innerMsg|errMsg):\s*([\s\S]*))?$/i.exec(message)
  const inner = upload || ticket
  if (inner) {
    const innerPlatformErrorCode = safePlatformCode(Number(inner[1]))
    if (innerPlatformErrorCode === undefined) return {}
    const platformReason = knownPlatformReason((inner[2] || '').trim())
      || (upload ? 'INNER_UPLOAD_FAILED' : 'TICKET_REQUEST_FAILED')
    return { innerPlatformErrorCode, platformReason,
      ...platformMessageDiagnostics(inner[2], depth + 1) }
  }
  const platformReason = knownPlatformReason(message)
  return platformReason === undefined ? {} : { platformReason }
}

function failure(code, failureStage, error) {
  const sdkCode = safeSdkCode(error && error.code)
  return { ok: false, code, failureStage, ...(sdkCode === undefined ? {} : { sdkCode }) }
}

function sdkFailure(error) {
  const code = error && error.code
  if (ENVIRONMENT_CODES.has(code)) return failure('ENVIRONMENT_FAILED', 'ENVIRONMENT', error)
  if (COMPILE_CODES.has(code) || (error && error.errorStage === 'builder')) {
    return failure('COMPILE_FAILED', 'COMPILE', error)
  }
  if (code === 20002) return failure('SIGNATURE_FAILED', 'VALIDATION', error)
  if ([10000, 30000].includes(code)) return failure('INPUT_INVALID', 'VALIDATION', error)
  const envelope = platformEnvelope(error)
  if (envelope) {
    return { ...failure('WECHAT_REJECTED', 'UPLOAD', error),
      platformErrorCode: envelope.errCode, ...platformMessageDiagnostics(envelope.errMsg) }
  }
  return failure('UPLOAD_UNKNOWN', 'UPLOAD', error)
}

async function uploadWithSdk(input, { ci, allowedRoot, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  if (!validInput(input)) return failure('INPUT_INVALID', 'VALIDATION')
  if (typeof allowedRoot !== 'string' || !path.isAbsolute(allowedRoot)) {
    return failure('WORKER_MISCONFIGURED', 'ENVIRONMENT')
  }
  const projectResult = projectCheck(
    input.projectPath, allowedRoot, input.appId, input.mode, input.targetAppId,
  )
  if (projectResult.code) return failure(projectResult.code, 'VALIDATION')
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 15 * 60 * 1000) {
    return failure('WORKER_MISCONFIGURED', 'ENVIRONMENT')
  }

  let timer
  try {
    const project = new ci.Project({
      appid: input.appId,
      type: 'miniProgram',
      projectPath: projectResult.project,
      privateKey: input.privateKey,
      ignores: ['node_modules/**/*'],
    })
    const upload = Promise.resolve().then(() => ci.upload({
      project,
      version: input.version,
      desc: input.description,
      setting: { useProjectConfig: true },
      ...(input.robot === undefined ? {} : { robot: input.robot }),
    }))
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject({ timeout: true }), timeoutMs)
    })
    await Promise.race([upload, timeout])
    return { ok: true, code: 'UPLOADED' }
  } catch (error) {
    return sdkFailure(error)
  } finally {
    clearTimeout(timer)
  }
}

function readInput() {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    process.stdin.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_INPUT_BYTES) {
        process.stdin.destroy()
        reject(new Error('input too large'))
        return
      }
      chunks.push(chunk)
    })
    process.stdin.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))) }
      catch { reject(new Error('invalid input')) }
    })
    process.stdin.on('error', reject)
  })
}

function writeResult(result) {
  fs.writeSync(1, `${JSON.stringify(result)}\n`)
}

async function main() {
  // The SDK can log project metadata or an exception. Only this wrapper writes a result.
  process.stdout.write = () => true
  process.stderr.write = () => true
  let result
  let input
  try {
    input = await readInput()
    if (!validInput(input)) result = failure('INPUT_INVALID', 'VALIDATION')
  } catch {
    result = failure('INPUT_INVALID', 'VALIDATION')
  }
  if (!result) {
    try {
      const ci = require('miniprogram-ci')
      result = await uploadWithSdk(input, { ci, allowedRoot: process.env.MINI_CI_ALLOWED_ROOT })
    } catch (error) {
      result = failure('ENVIRONMENT_FAILED', 'ENVIRONMENT', error)
    }
  }
  writeResult(result)
  process.exit(result.ok ? 0 : 1)
}

if (require.main === module) {
  main().catch(() => {
    writeResult({ ok: false, code: 'WORKER_ERROR' })
    process.exit(1)
  })
}

module.exports = { uploadWithSdk, safeDiagnostics, failure }
