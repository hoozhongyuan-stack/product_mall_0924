const test = require('node:test')
const assert = require('node:assert/strict')
const storage = new Map()
const writes = []
const removed = []
global.wx = {
  env: { USER_DATA_PATH: 'wxfile://usr' },
  getStorageSync: (key) => storage.get(key),
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key),
  getFileSystemManager: () => ({
    writeFile(options) { writes.push(options); options.success() },
    unlink(options) { removed.push(options.filePath); options.success && options.success() },
  }),
}
const voucherImage = require('../lib/voucher-image')

test('server PNG data URI is written as base64 user file and removed after use', async () => {
  storage.clear(); writes.length = 0; removed.length = 0
  const path = await voucherImage.write('data:image/png;base64,iVBORw0KGgo=')
  assert.match(path, /^wxfile:\/\/usr\/mall-voucher-[a-zA-Z0-9-]+\.png$/)
  assert.equal(writes[0].encoding, 'base64')
  assert.equal(writes[0].data, 'iVBORw0KGgo=')
  assert.deepEqual(storage.get('mall.voucherQrFiles.v1'), [path])
  voucherImage.release(path)
  assert.deepEqual(removed, [path])
  assert.equal(storage.has('mall.voucherQrFiles.v1'), false)
})

test('startup cleanup only deletes voucher files under the private user directory', () => {
  removed.length = 0
  storage.set('mall.voucherQrFiles.v1', ['wxfile://usr/mall-voucher-old.png', '/tmp/other.png'])
  voucherImage.cleanup()
  assert.deepEqual(removed, ['wxfile://usr/mall-voucher-old.png'])
  assert.equal(storage.has('mall.voucherQrFiles.v1'), false)
})

test('missing filesystem or malformed PNG keeps text-code fallback without writing', async () => {
  writes.length = 0
  await assert.rejects(voucherImage.write('data:image/svg+xml;base64,AA=='))
  const original = global.wx.getFileSystemManager
  delete global.wx.getFileSystemManager
  await assert.rejects(voucherImage.write('data:image/png;base64,iVBORw0KGgo='))
  global.wx.getFileSystemManager = original
  assert.equal(writes.length, 0)
})
