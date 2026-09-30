// Invoked only by sdk-offline-smoke.cjs in an isolated, network-disabled container.
const fs = require('node:fs')
const path = require('node:path')
process.stdout.write = () => true
process.stderr.write = () => true

async function main() {
  const ci = require('miniprogram-ci')
  const { getCompiler } = require('miniprogram-ci/dist/ci/getcompiler')
  const request = require('miniprogram-ci/dist/utils/request')
  // Block optional attribute/report lookups too; the container additionally uses --network none.
  request.request = async () => { throw Error('NETWORK_FORBIDDEN') }
  const projectPath = path.join(__dirname, 'fixtures/offline-project')
  const project = new ci.Project({ appid: 'wx0123456789abcdef', projectPath,
    type: 'miniProgram', privateKey: 'offline-placeholder-not-a-key' })
  project.attr = async () => ({ ...ci.DefaultProjectAttr })
  const compiler = await getCompiler(project, { useProjectConfig: true })
  try {
    const compiled = await compiler.compile({ setting: project.setting, resultType: 'prod',
      disableSpreadingUsingComponents: true, onProgressUpdate: () => {} })
    if (Object.keys(compiled).length === 0) throw Error('OFFLINE_CHECK_FAILED')
    // The source mount must remain read-only, while compilation cache is private/writable.
    try { fs.writeFileSync(path.join(projectPath, 'write-check'), 'forbidden'); throw Error('SOURCE_WRITABLE') }
    catch (error) { if (error.code !== 'EROFS') throw error }
    if ((fs.statSync(process.cwd()).mode & 0o777) !== 0o700
      || process.env.HOME !== process.cwd() || process.env.TMPDIR !== process.cwd()) {
      throw Error('WORKSPACE_ISOLATION_FAILED')
    }
    fs.writeSync(1, JSON.stringify({ ok: true, code: 'COMPILED_OFFLINE' }))
  } finally { compiler.destroy() }
}
main().then(() => process.exit(0)).catch((error) => {
  fs.writeSync(1, JSON.stringify({ ok: false, code: 'OFFLINE_CHECK_FAILED',
    sdkCode: require('../upload').safeDiagnostics({ sdkCode: error.code || error.message }).sdkCode }))
  process.exit(1)
})
