const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { uploadWithSdk } = require('../upload')

const APP_ID = 'wx0123456789abcdef'

function projectFixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-test-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const projectPath = path.join(root, 'project')
  fs.mkdirSync(projectPath)
  fs.writeFileSync(path.join(projectPath, 'project.config.json'), JSON.stringify({ appid: APP_ID }))
  return { root, projectPath }
}

function input(projectPath) {
  return {
    mode: 'CI_DIRECT',
    appId: APP_ID,
    projectPath,
    privateKey: '-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----',
    version: '1.0.1',
    description: 'release candidate',
    robot: 1,
  }
}

test('passes validated in-root project and private key to official API shape', async (t) => {
  const { root, projectPath } = projectFixture(t)
  let projectOptions
  let uploadOptions
  const ci = {
    Project: class {
      constructor(options) { projectOptions = options }
    },
    upload: async (options) => { uploadOptions = options; return { unexpected: 'do not expose' } },
  }
  const result = await uploadWithSdk(input(projectPath), { ci, allowedRoot: root })
  assert.deepEqual(result, { ok: true, code: 'UPLOADED' })
  assert.equal(projectOptions.appid, APP_ID)
  assert.equal(projectOptions.privateKey, input(projectPath).privateKey)
  assert.equal(projectOptions.type, 'miniProgram')
  assert.equal(uploadOptions.version, '1.0.1')
  assert.equal(uploadOptions.desc, 'release candidate')
  assert.equal(uploadOptions.robot, 1)
  assert.deepEqual(uploadOptions.setting, { useProjectConfig: true })
})

test('rejects project paths outside allowed root before SDK invocation', async (t) => {
  const { projectPath } = projectFixture(t)
  const allowedRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mini-ci-other-'))
  t.after(() => fs.rmSync(allowedRoot, { recursive: true, force: true }))
  const result = await uploadWithSdk(input(projectPath), {
    ci: { Project: class { constructor() { throw Error('called') } } }, allowedRoot,
  })
  assert.deepEqual(result, { ok: false, code: 'PROJECT_INVALID', failureStage: 'VALIDATION' })
})

test('rejects symlinks inside the project tree before SDK invocation', async (t) => {
  const { root, projectPath } = projectFixture(t)
  fs.symlinkSync('/etc/passwd', path.join(projectPath, 'unexpected-link'))
  const result = await uploadWithSdk(input(projectPath), {
    ci: { Project: class { constructor() { throw Error('called') } } }, allowedRoot: root,
  })
  assert.deepEqual(result, { ok: false, code: 'PROJECT_INVALID', failureStage: 'VALIDATION' })
})

test('blocks AppID mismatch and malformed user input', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const mismatch = { ...input(projectPath), appId: 'wx1111111111111111' }
  assert.deepEqual(await uploadWithSdk(mismatch, { ci: {}, allowedRoot: root }), {
    ok: false, code: 'PROJECT_APPID_MISMATCH', failureStage: 'VALIDATION',
  })
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), robot: 31 }, {
    ci: {}, allowedRoot: root,
  }), { ok: false, code: 'INPUT_INVALID', failureStage: 'VALIDATION' })
})

test('requires explicit target AppID for directCommit and matches ext.json', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const targetAppId = 'wx1111111111111111'
  const extPath = path.join(projectPath, 'ext.json')
  fs.writeFileSync(extPath, JSON.stringify({
    extEnable: true, extAppid: targetAppId, directCommit: true,
  }))
  const ci = { Project: class {}, upload: async () => ({}) }
  assert.deepEqual(await uploadWithSdk(input(projectPath), { ci, allowedRoot: root }), {
    ok: false, code: 'TARGET_CONFIG_INVALID', failureStage: 'VALIDATION',
  })
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), mode: 'DIRECT_COMMIT', targetAppId }, {
    ci, allowedRoot: root,
  }), { ok: true, code: 'UPLOADED' })
  fs.writeFileSync(extPath, JSON.stringify({
    extEnable: true, extAppid: targetAppId, directCommit: false,
  }))
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), mode: 'DIRECT_COMMIT', targetAppId }, {
    ci, allowedRoot: root,
  }), { ok: false, code: 'TARGET_CONFIG_INVALID', failureStage: 'VALIDATION' })
})

test('direct mode requires ext.json and direct target, while CI_DIRECT rejects ext.json', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const ci = { Project: class {}, upload: async () => ({}) }
  const targetAppId = 'wx1111111111111111'
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), mode: 'DIRECT_COMMIT', targetAppId }, {
    ci, allowedRoot: root,
  }), { ok: false, code: 'TARGET_CONFIG_INVALID', failureStage: 'VALIDATION' })
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), targetAppId }, {
    ci, allowedRoot: root,
  }), { ok: false, code: 'INPUT_INVALID', failureStage: 'VALIDATION' })
  assert.deepEqual(await uploadWithSdk({ ...input(projectPath), mode: 'NOT_A_MODE' }, {
    ci, allowedRoot: root,
  }), { ok: false, code: 'INPUT_INVALID', failureStage: 'VALIDATION' })
})

test('does not expose SDK errors, private keys, or arbitrary SDK results', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const secret = input(projectPath).privateKey
  const ci = {
    Project: class {},
    upload: async () => { throw Object.assign(new Error(`SDK failed ${secret}`), { errCode: 85001 }) },
  }
  const result = await uploadWithSdk(input(projectPath), { ci, allowedRoot: root })
  assert.deepEqual(result, { ok: false, code: 'WECHAT_REJECTED', failureStage: 'UPLOAD', platformErrorCode: 85001 })
  assert.equal(JSON.stringify(result).includes(secret), false)
})

test('timeout is an unknown platform outcome', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const ci = { Project: class {}, upload: () => new Promise(() => {}) }
  const result = await uploadWithSdk(input(projectPath), {
    ci, allowedRoot: root, timeoutMs: 5,
  })
  assert.deepEqual(result, { ok: false, code: 'UPLOAD_UNKNOWN', failureStage: 'UPLOAD' })
})

test('known SDK local and platform errors retain safe stages and codes', async (t) => {
  const { root, projectPath } = projectFixture(t)
  const cases = [
    [{ code: 'EROFS' }, { code: 'ENVIRONMENT_FAILED', failureStage: 'ENVIRONMENT', sdkCode: 'EROFS' }],
    [{ code: 'ENOSPC' }, { code: 'ENVIRONMENT_FAILED', failureStage: 'ENVIRONMENT', sdkCode: 'ENOSPC' }],
    [{ code: 10006 }, { code: 'COMPILE_FAILED', failureStage: 'COMPILE', sdkCode: '10006' }],
    [{ code: 20003, errorStage: 'builder' }, { code: 'COMPILE_FAILED', failureStage: 'COMPILE', sdkCode: '20003' }],
    [{ code: 20002 }, { code: 'SIGNATURE_FAILED', failureStage: 'VALIDATION', sdkCode: '20002' }],
    [{ code: 20003, errorStage: 'backend', errCode: -10002 },
      { code: 'WECHAT_REJECTED', failureStage: 'UPLOAD', sdkCode: '20003', platformErrorCode: -10002 }],
    [{ code: 20003, message: 'Error: {"errCode":-10002,"errMsg":"secret invalid ip"}' },
      { code: 'WECHAT_REJECTED', failureStage: 'UPLOAD', sdkCode: '20003', platformErrorCode: -10002 }],
    [{ code: 20003, message: 'Error: Error: errCode: 40013; errMsg: secret' },
      { code: 'WECHAT_REJECTED', failureStage: 'UPLOAD', sdkCode: '20003', platformErrorCode: 40013 }],
    [{ code: 20003, message: 'network unknown secret' },
      { code: 'UPLOAD_UNKNOWN', failureStage: 'UPLOAD', sdkCode: '20003' }],
    [{ code: 'ECONNRESET' },
      { code: 'UPLOAD_UNKNOWN', failureStage: 'UPLOAD', sdkCode: 'ECONNRESET' }],
  ]
  for (const [error, expected] of cases) {
    const ci = { Project: class {}, upload: async () => { throw error } }
    assert.deepEqual(await uploadWithSdk(input(projectPath), { ci, allowedRoot: root }),
      { ok: false, ...expected })
  }
})

test('does not infer platform rejection from arbitrary error messages or unsafe fields', async (t) => {
  const { root, projectPath } = projectFixture(t)
  for (const error of [
    { code: 20003, message: 'unknown error includes errCode: -10002' },
    { code: 20003, message: 'Error: {"errCode":0,"errMsg":"secret"}' },
    { code: 20003, message: 'Error: {"errCode":100000000,"errMsg":"secret"}' },
    { code: 20003, message: `Error: {"errCode":-10002,"errMsg":"${'s'.repeat(17000)}"}` },
    { code: 'https://secret', errorStage: 'secret-stage', errCode: '40013' },
  ]) {
    const ci = { Project: class {}, upload: async () => { throw error } }
    const result = await uploadWithSdk(input(projectPath), { ci, allowedRoot: root })
    assert.equal(result.code, 'UPLOAD_UNKNOWN')
    assert.equal(result.platformErrorCode, undefined)
    assert.equal(JSON.stringify(result).includes('secret'), false)
  }
})
