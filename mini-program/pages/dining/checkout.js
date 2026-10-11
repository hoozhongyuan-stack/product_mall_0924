const api = require('../../lib/api')
const dining = require('../../lib/dining-alliance')
const orders = require('../../lib/orders')
const cart = require('../../lib/cart')
const { money } = require('../../lib/catalog')
const KEY = 'mall.diningPendingOrder.v1'
function removeOwned(pending) {
 const saved = wx.getStorageSync(KEY)
 if (wx.getStorageSync('mall.memberToken') === pending.identity && saved?.identity === pending.identity && saved.key === pending.key) wx.removeStorageSync(KEY)
}
Page({
 data: { state:'loading', error:'', quote:null, lines:[], total:'', goodsTotal:'', shippingFee:'', paymentChoices:[], paymentMethod:'', canSubmit:false, busy:false, pending:false },
 onLoad() {
  this.identity = wx.getStorageSync('mall.memberToken'); this.active = true
  const stored = wx.getStorageSync(KEY)
  this.pending = stored?.identity === this.identity ? stored : null
  if (this.pending) { this.setData({state:'uncertain',pending:true,error:'上次提交结果待确认，请重试原申请或查看订单。'}); return }
  return this.refresh()
 },
 onShow() {
  this.active = true
  const identity = wx.getStorageSync('mall.memberToken')
  if (this.identity !== identity) {
   this.generation = (this.generation || 0) + 1; this.identity = identity
   const stored = wx.getStorageSync(KEY); this.pending = stored?.identity === identity ? stored : null
   this.setData({state:this.pending?'uncertain':'expired',quote:null,lines:[],total:'',goodsTotal:'',shippingFee:'',busy:false,pending:!!this.pending,canSubmit:false,error:'登录状态已改变，请查看本人订单或重新扫码。'})
  } else if (this.shown && !this.pending && !dining.current()) this.setData({state:'expired',quote:null,lines:[],canSubmit:false,error:'餐桌访问已结束，请重新扫码。'})
  else if (this.shown && this.pending) this.setData({state:'uncertain',busy:false,pending:true,canSubmit:false,error:'提交结果待确认，请重试原请求。'})
  this.shown = true
 },
 onHide() { this.active = false; this.generation = (this.generation || 0) + 1; this.setData({quote:null,lines:[],canSubmit:false}) },
 onUnload() { this.onHide() },
 valid(g, identity) { return this.active && g === this.generation && identity === this.identity && identity === wx.getStorageSync('mall.memberToken') },
 async refresh() {
  if (this.pending || this.data.busy) return
  const visit = dining.current()
  if (!visit) { this.setData({state:'expired',error:'请重新扫描餐桌码。',canSubmit:false}); return }
  const g = this.generation = (this.generation || 0) + 1, identity = this.identity
  this.setData({state:'loading',canSubmit:false,error:''})
  try {
   const quote = await api.post('/api/v1/app/checkout/quotes',{diningSessionId:visit.sessionId,items:cart.quoteItems(cart.selected())})
   if (!this.valid(g,identity) || dining.current()?.sessionId !== visit.sessionId) return
   const choices = (quote.availablePaymentMethods || []).filter(v => ['WECHAT','OFFLINE'].includes(v))
   const method = choices.includes(this.data.paymentMethod) ? this.data.paymentMethod : choices[0] || ''
   this.setData({state:'ready',quote,lines:(quote.lines || []).map(l=>({...l,amount:money(l.lineAmountFen)})),total:money(quote.payableFen),goodsTotal:money(quote.goodsTotalFen),shippingFee:money(quote.shippingFeeFen),paymentChoices:choices,paymentMethod:method,canSubmit:!!method&&dining.quoteReady(quote),error:quote.confirmRequired?'价格已变化，请确认新价格。':!quote.ready?'商品缺货或已下架，请返回调整。':''})
  } catch (e) { if (this.valid(g,identity)) this.setData({state:'error',error:e.message,canSubmit:false}) }
 },
 choosePayment(e) { const value=e.detail.value; if(this.data.paymentChoices.includes(value)) this.setData({paymentMethod:value,canSubmit:dining.quoteReady(this.data.quote)}) },
 confirmPrices() { for(const l of this.data.quote.lines) { const old=dining.items().find(x=>x.skuId===l.skuId); if(old)dining.select({...old,seenPriceFen:l.unitPriceFen}) } return this.refresh() },
 async submit() {
  if(this.data.busy || (!this.pending && !this.data.canSubmit)) return
  if(this.identity !== wx.getStorageSync('mall.memberToken')) { this.setData({error:'登录已改变，请查看本人订单。',canSubmit:false}); return }
  try {
   if(!this.pending) { const intent={identity:this.identity,key:orders.requestKey(),body:{quoteId:this.data.quote.quoteId,paymentMethod:this.data.paymentMethod}}; wx.setStorageSync(KEY,intent); this.pending=intent }
  } catch(e) { this.setData({error:'无法保存提交凭证，请检查设备存储后重试。'}); return }
  const pending = {...this.pending,body:{...this.pending.body}}, identity=pending.identity, g=this.generation, visitId=dining.current()?.sessionId
  this.setData({busy:true,pending:true,error:''})
  try {
   const result=await api.post('/api/v1/app/orders',pending.body,{'Idempotency-Key':pending.key})
   if(!result?.orderId) throw new Error('订单响应不完整，请重试原请求。')
   removeOwned(pending)
   if (!this.valid(g,identity)) return
   if(this.pending?.key===pending.key) this.pending=null
   if(dining.current()?.sessionId===visitId) await dining.close()
   if(!this.valid(g,identity)) return
   this.setData({state:'submitted',pending:false,canSubmit:false,busy:false})
   wx.redirectTo({url:`/pages/orders/detail?id=${encodeURIComponent(result.orderId)}&result=1`,fail:()=>{if(this.valid(g,identity))this.setData({error:'订单已创建，请从我的订单查看。'})}})
  } catch(e) {
   const definite=e.statusCode&&e.statusCode<500&&![401,408,429].includes(e.statusCode)
   if(definite) removeOwned(pending)
   if(!this.valid(g,identity)) return
   if(definite) { if(this.pending?.key===pending.key)this.pending=null; this.setData({state:'error',pending:false,canSubmit:false,error:e.message}) }
   else this.setData({state:'uncertain',canSubmit:false,error:'提交结果待确认，请重试原请求，避免重复下单。'})
  } finally { if(this.valid(g,identity))this.setData({busy:false}) }
 },
 openOrders() { dining.close(); wx.navigateTo({url:'/pages/orders/list'}) }
})
