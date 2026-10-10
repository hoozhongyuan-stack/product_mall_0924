const test = require('node:test'), assert = require('node:assert/strict')
const base = 'https://mall.test', id = '11111111-1111-4111-8111-111111111111', pageId = '22222222-2222-4222-8222-222222222222', versionId = '33333333-3333-4333-8333-333333333333'
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: base } })
const row = { id, title: '活动券', kind: 'CASH', minGoodsFen: 0, discountFen: 500, productIds: [], productNames: [], redeemEligible: true, validFrom: '2026-01-01T00:00:00Z', validUntil: '2027-01-01T00:00:00Z', remainingQuantity: 5, selfClaimLimit: 1, selfClaimedCount: 0, canClaim: true, claimState: 'AVAILABLE' }
const config = { schemaVersion: 4, components: [{ componentId: 'c', type: 'COUPON_LIST', visible: true, props: { source: 'MANUAL', campaignIds: [id], limit: 10, layout: 'LIST' } }] }
function reset() { storage.clear(); global.wx = { getStorageSync: key => storage.get(key), setStorageSync: (key,value) => storage.set(key,value), removeStorageSync: key => storage.delete(key), hideShareMenu() {}, showShareMenu() {}, setNavigationBarTitle() {} } }
const ok = (req, data) => req.success({ statusCode: 200, data: { success: true, data } })
const data = (rows=[row], memberId='m1') => ({ pageId, versionId, memberId, componentData: { c: rows } })
function mount() { let d; global.Page = v => { d = v }; const file = '../pages/micro/detail.js'; delete require.cache[require.resolve(file)]; require(file); return { ...d, data: structuredClone(d.data), setData(p) { this.data = { ...this.data, ...p } } } }
const tap = () => ({ currentTarget: { dataset: { component: 'c', id } } })
async function ready(p, rows=[row], member='m1') { wx.request = req => ok(req, req.url.includes('/coupons?') ? data(rows,member) : { pageId, versionId, name: '优惠专区', config }); await p.onLoad({ pageId }) }
test('schema4 coupon component requests one version-bound hydration and renders every authoritative claim state', async () => {
  reset(); const p = mount(), urls = []
  wx.request = req => { urls.push(req.url); ok(req, req.url.includes('/coupons?') ? data([{...row,canClaim:false,claimState:'GUEST'}],null) : { pageId, versionId, name: '优惠专区', config }) }
  await p.onLoad({ pageId })
  assert.match(urls[0], /schemaVersion=4/)
  assert.equal(urls[1], `${base}/api/v1/app/pages/${pageId}/coupons?versionId=${versionId}`)
  assert.equal(p.data.couponData.c[0].claimLabel, '登录后领取')
  for (const [state,label] of [['SOLD_OUT','已抢光'],['EXPIRED','已过期'],['NOT_STARTED','尚未开始'],['UNAVAILABLE','暂不可领取'],['LIMIT_REACHED','已达领取上限']]) {
    await ready(p,[{...row,canClaim:false,claimState:state}],null)
    assert.equal(p.data.couponData.c[0].claimLabel,label)
  }
})
test('direct embedded claim reuses owner-bound durable UUID and UNKNOWN recovery exactly once', async () => {
  reset(); storage.set('mall.memberToken','A'); const p=mount(); await ready(p)
  const requests=[]; wx.request=req=>requests.push(req)
  const first=p.claimCoupon(tap()); p.claimCoupon(tap())
  assert.equal(requests.length,1); const pending=storage.get('mall.couponClaims.v1').m1
  assert.equal(requests[0].header['Idempotency-Key'],pending.key)
  requests[0].fail(); await first
  assert.equal(p.data.couponClaim.pending.key,pending.key)
  const recovery=p.retryCouponClaim(); assert.match(requests[1].url,new RegExp(`/coupon-claims/${pending.key}$`))
  ok(requests[1],{status:'COMPLETED',result:{campaignId:id,couponIds:['owned'],quantity:1}})
  await new Promise(resolve=>setImmediate(resolve)); assert.equal(requests[2].method,'GET'); ok(requests[2],data([{...row,canClaim:false,claimState:'LIMIT_REACHED',selfClaimedCount:1}]))
  await recovery; assert.equal(p.data.couponClaim.pending,null); assert.match(p.data.couponClaim.message,/领取成功/)
})
test('page/version mismatch, member switch and hide never publish stale private coupon state', async () => {
  reset(); storage.set('mall.memberToken','A'); const p=mount(); await ready(p)
  wx.request=req=>ok(req,{...data(),versionId:'other'}); await p.reloadCoupons()
  assert.equal(p.data.couponState,'error'); assert.deepEqual(p.data.couponData,{})
  await ready(p); let req; wx.request=r=>{ req=r }; const claim=p.claimCoupon(tap())
  storage.set('mall.memberToken','B'); ok(req,{campaignId:id,couponIds:['private'],quantity:1}); await claim
  assert.deepEqual(p.data.couponData,{}); assert.equal(p.data.couponClaim.pending,null)
  await ready(p); let late; wx.request=r=>{late=r}; const refresh=p.reloadCoupons(); p.onHide(); ok(late,data()); await refresh
  assert.deepEqual(p.data.couponData,{})
})
test('no coupons avoids hydration; guest opens login; invalid events do not claim', async () => {
  reset(); const p=mount(); let calls=0, destination=''
  wx.request=req=>{calls++;ok(req,{pageId,versionId,name:'普通页',config:{schemaVersion:1,components:[]}})}
  await p.onLoad({pageId}); assert.equal(calls,1)
  await ready(p,[{...row,claimState:'GUEST',canClaim:false}],null); wx.navigateTo=({url})=>{destination=url}
  p.claimCoupon({currentTarget:{dataset:{component:'evil',id}}}); assert.equal(destination,'')
  p.claimCoupon(tap()); assert.match(destination,/login/)
})

test('malformed hydration, failed retry and quota refusal preserve safe states', async () => {
  reset(); storage.set('mall.memberToken','A'); const p=mount(); await ready(p)
  wx.request=req=>ok(req,data([{...row,claimState:'INVENTED'}])); await p.reloadCoupons()
  assert.equal(p.data.couponState,'error'); assert.deepEqual(p.data.couponData,{})
  wx.request=req=>req.success({statusCode:409,data:{success:false,error:{code:'PAGE_VERSION_CHANGED',message:'changed'}}}); await p.reloadCoupons()
  assert.match(p.data.couponError,/页面已更新/)
  await ready(p); wx.request=req=>req.success({statusCode:422,data:{success:false,error:{message:'已抢光'}}}); await p.claimCoupon(tap())
  assert.equal(p.data.couponClaim.pending,null); assert.match(p.data.couponClaim.claimError,/抢光/)
  await ready(p); wx.request=req=>req.fail(); await p.claimCoupon(tap())
  const key=p.data.couponClaim.pending.key; await p.retryCouponClaim()
  assert.equal(p.data.couponClaim.pending.key,key)
  assert.match(p.data.couponClaim.claimError,/尚未确认/)
})
test('foreign member pending is not shown and guest canClaim flag is rejected', async () => {
  reset(); storage.set('mall.memberToken','A'); storage.set('mall.couponClaims.v1',{other:{memberId:'other',campaignId:id,key:'44444444-4444-4444-8444-444444444444'}})
  const p=mount(); await ready(p); assert.equal(p.data.couponClaim.pending,null)
  storage.delete('mall.memberToken'); await ready(p,[row],null)
  assert.equal(p.data.couponState,'error'); assert.deepEqual(p.data.couponData,{})
})

test('private or unavailable scoped products do not turn a restricted coupon into a site-wide coupon', async () => {
  reset(); const p=mount()
  await ready(p,[{...row,scopeRestricted:true,productIds:[],productNames:[],canClaim:false,claimState:'GUEST'}],null)
  assert.equal(p.data.couponData.c[0].scopeLabel,'指定商品适用')
})

test('coupon version conflict retry reloads the published page before another hydration', async () => {
  reset(); const p=mount(); await ready(p,[{...row,canClaim:false,claimState:'GUEST'}],null)
  wx.request=req=>req.success({statusCode:409,data:{success:false,error:{message:'页面已更新'}}}); await p.reloadCoupons()
  const calls=[]; wx.request=req=>{calls.push(req.url);ok(req,req.url.includes('/coupons?')?data([{...row,canClaim:false,claimState:'GUEST'}],null):{pageId,versionId,name:'新版',config})}
  await p.reloadCoupons()
  assert.match(calls[0],new RegExp(`/pages/${pageId}\\?schemaVersion=4$`))
  assert.match(calls[1],/coupons\?versionId=/)
})
