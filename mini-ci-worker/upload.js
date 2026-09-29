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

function safePlatformCode(error) {
  const value = error && error.errCode
  return Number.isInteger(value) && value > 0 && value <= 99999999 ? value : undefined
}

async function uploadWithSdk(input, { ci, allowedRoot, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  if (!validInput(input)) return { ok: false, code: 'INPUT_INVALID' }
  if (typeof allowedRoot !== 'string' || !path.isAbsolute(allowedRoot)) {
    return { ok: false, code: 'WORKER_MISCONFIGURED' }
  }
  const projectResult = projectCheck(
    input.projectPath, allowedRoot, input.appId, input.mode, input.targetAppId,
  )
  if (projectResult.code) return { ok: false, code: projectResult.code }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 15 * 60 * 1000) {
    return { ok: false, code: 'WORKER_MISCONFIGURED' }
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
    if (error && error.timeout === true) return { ok: false, code: 'UPLOAD_UNKNOWN' }
    const platformErrorCode = safePlatformCode(error)
    return platformErrorCode === undefined
      ? { ok: false, code: 'UPLOAD_FAILED' }
      : { ok: false, code: 'UPLOAD_FAILED', platformErrorCode }
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
  try {
    const input = await readInput()
    const ci = require('miniprogram-ci')
    result = await uploadWithSdk(input, {
      ci,
      allowedRoot: process.env.MINI_CI_ALLOWED_ROOT,
    })
  } catch {
    result = { ok: false, code: 'INPUT_OR_WORKER_ERROR' }
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

module.exports = { uploadWithSdk }
