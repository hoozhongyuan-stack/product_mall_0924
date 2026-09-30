const test = require('node:test'), assert = require('node:assert/strict')
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = { getStorageSync: (key) => storage.get(key), removeStorageSync: (key) => storage.delete(key), navigateTo() {}, stopPullDownRefresh() {} }
const overview = { id: 'member-A', grade: { name: '普通会员' }, enabled: true, effectiveSpendFen: 1500,
 points: { settledPoints: -20, frozenPoints: 0, availablePoints: 0, debtPoints: 20, expiredPendingPoints: 0, expiringPoints: 0, nextExpiryAt: null },
 rules: { revision: 3, grades: [{ name: '普通会员', minimumSpendFen: 0 }], points: { earnUnitFen: 100, earnPoints: 1, deductPoints: 100, deductFen: 100, maxPercent: 20, validDays: 365, refundValidDays: 7 } } }
const entry = (id, kind = 'EARN', effect = 'BALANCE') => ({ id, kind, effect, amount: 20, balance: effect === 'BALANCE' ? 20 : null, orderId: 'order-A', createdAt: '2026-09-27T10:00:00Z', expiresAt: null })
const list = (items, page = 1, total = items.length) => ({ items, pagination: { page, pageSize: 20, total } })
function success(r, data) { r.success({ statusCode: 200, data: { success: true, data } }) }
function failure(r, status, message) { r.success({ statusCode: status, data: { success: false, error: { message } } }) }
function pageAt(name = 'index') { let d; global.Page = (v) => { d = v }; const p = `../pages/member/${name}.js`; delete require.cache[require.resolve(p)]; require(p); return { ...d, data: structuredClone(d.data), setData(patch) { this.data = { ...this.data, ...patch } } } }
function prime(name) { storage.clear(); storage.set('mall.memberToken', 'A'); return pageAt(name) }
test('member presentation preserves debt and explains historical snapshots', () => { const m = require('../lib/member'); const r = m.presentOverview(overview); assert.equal(r.spendLabel, '¥15'); assert.equal(r.points.debtPoints, 20); assert.match(r.snapshotCopy, /历史/); assert.throws(() => m.presentOverview({ ...overview, points: { ...overview.points, availablePoints: -2 } }), /会员/) })
test('freeze and release never pretend to be balance awards', () => { const m = require('../lib/member'); assert.equal(m.presentPoint(entry('r', 'RESERVE', 'FREEZE')).balanceLabel, ''); assert.match(m.presentPoint(entry('u', 'RELEASE', 'RELEASE')).kindLabel, /释放/); assert.match(m.presentPoint(entry('f', 'FUTURE')).kindLabel, /积分变动/) })
test('unauthenticated member page clears details', async () => { const p = prime(); storage.clear(); let n = 0; wx.request = () => { n++ }; await p.onShow(); assert.equal(n, 0); assert.equal(p.data.state, 'auth'); assert.equal(p.data.member, null) })
test('late profile response cannot restore after logout or unload', async () => { let p = prime(), r; wx.request = (v) => { r = v }; const a = p.onShow(); storage.clear(); success(r, overview); await a; assert.equal(p.data.member, null); p = prime(); const b = p.onShow(); p.onUnload(); success(r, overview); await b; assert.equal(p.data.member, null) })
test('older response cannot replace latest refresh', async () => { const p = prime(), rs = []; wx.request = (v) => rs.push(v); const a = p.onShow(), b = p.refresh(); success(rs[1], { ...overview, effectiveSpendFen: 2500 }); await b; success(rs[0], overview); await a; assert.equal(p.data.member.spendLabel, '¥25') })
test('old session response cannot erase new member account', async () => { const p = prime(), rs = []; wx.request = (v) => rs.push(v); const a = p.onShow(); storage.set('mall.memberToken', 'B'); const b = p.onShow(); success(rs[1], { ...overview, id: 'B' }); await b; success(rs[0], overview); await a; assert.equal(p.data.member.id, 'B') })
test('pagination blocks duplicate requests and retries failed next page', async () => { const p = prime('points'), rs = []; wx.request = (v) => rs.push(v); const a = p.onShow(); success(rs[0], list([entry('1')], 1, 2)); await a; const b = p.loadMore(); p.loadMore(); assert.equal(rs.length, 2); rs[1].fail(); await b; assert.equal(p.data.rows.length, 1); const c = p.loadMore(); assert.match(rs[2].url, /page=2/); success(rs[2], list([entry('2')], 2, 2)); await c; assert.equal(p.data.rows.length, 2) })
test('empty malformed and service failure are distinct states', async () => { const p = prime('points'); wx.request = (r) => success(r, list([])); await p.onShow(); assert.equal(p.data.state, 'empty'); wx.request = (r) => success(r, { items: [] }); await p.refresh(); assert.equal(p.data.state, 'error'); wx.request = (r) => failure(r, 503, '服务暂不可用'); await p.refresh(); assert.match(p.data.error, /服务/) })
test('refresh discards stale page and expired auth clears rows', async () => { const p = prime('points'); wx.request = (r) => success(r, list([entry('1')], 1, 2)); await p.onShow(); const rs = []; wx.request = (v) => rs.push(v); const a = p.loadMore(), b = p.refresh(); success(rs[1], list([entry('new')])); await b; success(rs[0], list([entry('old')], 2, 2)); await a; assert.equal(p.data.rows[0].id, 'new'); wx.request = (r) => failure(r, 401, '登录已失效'); await p.refresh(); assert.equal(p.data.state, 'auth'); assert.equal(p.data.rows.length, 0) })
test('consumption retains signed adjustments and traces grade downgrade with order', () => { const m = require('../lib/member'); const r = m.presentConsumption({ id: 'c1', orderId: 'o1', amountFen: -500, balanceFen: 1000, gradeBefore: { name: '金卡' }, gradeAfter: { name: '普通会员' }, createdAt: '2026-09-27T10:00:00Z' }); assert.equal(r.amountLabel, '-¥5'); assert.match(r.gradeLabel, /金卡.*普通会员/); assert.equal(r.balanceLabel, '¥10') })
test('hidden list discards in-flight data and blocks late pagination from member switch', async () => { const p = prime('points'); let r; wx.request = (v) => { r = v }; const a = p.onShow(); p.onHide(); success(r, list([entry('private')])); await a; assert.equal(p.data.rows.length, 0); wx.request = (v) => success(v, list([entry('initial')], 1, 2)); await p.onShow(); wx.request = (v) => { r = v }; const b = p.loadMore(); storage.set('mall.memberToken', 'B'); success(r, list([entry('old')], 2, 2)); await b; assert.equal(p.data.rows.length, 0); assert.equal(p.data.state, 'auth') })
test('member controls open implemented routes and login returns to the calling page', async () => {
 const p = prime(), urls = []; wx.navigateTo = ({ url }) => urls.push(url)
 p.openPoints(); p.openConsumption(); p.openOrders(); p.login(); p.openOrder({ currentTarget: { dataset: { id: 'a/b' } } })
 assert.deepEqual(urls, ['/pages/member/points', '/pages/member/consumption', '/pages/orders/list', '/pages/login/login?returnTo=member', '/pages/orders/detail?id=a%2Fb'])
 let stopped = 0; wx.stopPullDownRefresh = () => { stopped++ }; wx.request = (r) => success(r, overview)
 await p.onPullDownRefresh(); assert.equal(stopped, 1); assert.equal(p.data.state, 'ready'); p.onReachBottom()
})
test('consumption page reads only本人 endpoint and renders non-reward adjustment', async () => {
 const p = prime('consumption'); wx.request = (r) => { assert.match(r.url, /\/app\/member\/consumption\?page=1&pageSize=20$/); success(r, list([{ id: 'c', orderId: 'o', amountFen: 0, balanceFen: 0, gradeBefore: { name: '普通会员' }, gradeAfter: { name: '普通会员' }, createdAt: '2026-09-27T10:00:00Z' }])) }
 await p.onShow(); assert.equal(p.data.rows[0].amountLabel, '¥0'); assert.match(p.data.rows[0].gradeLabel, /保持/)
})
test('public consumption causes explain completion refunds and reversal without leaking internal source', () => {
 const m = require('../lib/member'), base = { id: 'c', orderId: 'o', amountFen: 0, balanceFen: 0, gradeBefore: { name: '普通会员' }, gradeAfter: { name: '普通会员' }, createdAt: '2026-09-27T10:00:00Z' }
 assert.match(m.presentConsumption({ ...base, sourceRef: 'ORDER_COMPLETED' }).reasonLabel, /履约完成/)
 assert.match(m.presentConsumption({ ...base, sourceRef: 'REFUND_COMPLETED' }).reasonLabel, /退款完成/)
 assert.match(m.presentConsumption({ ...base, sourceRef: 'FULFILLMENT_REVERSED' }).reasonLabel, /核销撤销/)
 assert.match(m.presentConsumption({ ...base, sourceRef: 'private:any' }).reasonLabel, /订单结算调整/)
 assert.match(m.presentOverview({ ...overview, gradePolicyRevision: 2 }).gradeRuleCopy, /2/)
 assert.match(m.presentOverview({ ...overview, gradePolicyRevision: 0 }).gradeRuleCopy, /尚未/)
 assert.match(m.presentPoint({ ...entry('x', 'CLAWBACK'), sourceRef: 'REFUND_COMPLETED' }).reasonLabel, /退款完成/)
})

test('guest member page keeps public shortcuts and protects private destinations', async () => {
 const p = prime(), urls = []
 storage.clear(); wx.navigateTo = ({ url }) => urls.push(url)
 wx.request = () => assert.fail('guest overview must not be requested')
 await p.onShow()
 assert.equal(p.data.state, 'auth')
 assert.equal(p.data.member, null)
 p.openOrders({ currentTarget: { dataset: { status: 'PENDING_PAYMENT' } } })
 p.openPoints()
 p.openExchange()
 assert.deepEqual(urls, [
  '/pages/login/login?returnTo=member&next=orders&status=PENDING_PAYMENT',
  '/pages/login/login?returnTo=member&next=points',
  '/pages/exchange/index',
 ])
})

test('expired member session cannot open private entries', async () => {
 const p = prime(), urls = []
 wx.navigateTo = ({ url }) => urls.push(url)
 wx.request = (r) => failure(r, 401, '登录已失效')
 await p.onShow()
 assert.equal(p.data.member, null)
 p.openAddresses()
 assert.deepEqual(urls, ['/pages/login/login?returnTo=member&next=addresses'])
})

test('member login validates a return destination and restores the selected order filter', async () => {
 let page
 global.Page = (value) => { page = value }
 delete require.cache[require.resolve('../pages/login/login.js')]
 require('../pages/login/login.js')
 const p = { ...page, data: { ...page.data, agreed: true }, setData(patch) { this.data = { ...this.data, ...patch } } }
 const urls = []
 wx.login = ({ success }) => success({ code: 'code' })
 wx.setStorageSync = (key, value) => storage.set(key, value)
 wx.request = (r) => success(r, { accessToken: 'fresh' })
 wx.redirectTo = ({ url }) => urls.push(url)
 wx.navigateBack = () => urls.push('back')
 p.onLoad({ returnTo: 'member', next: 'orders', status: 'PENDING_PAYMENT' })
 await p.login()
 assert.deepEqual(urls, ['/pages/orders/list?status=PENDING_PAYMENT'])
 assert.equal(storage.get('mall.memberToken'), 'fresh')
 p.onLoad({ returnTo: 'member', next: 'https://evil.invalid' })
 await p.login()
 assert.deepEqual(urls.slice(-1), ['back'])
})
