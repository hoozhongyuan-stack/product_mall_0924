const api = require('../../lib/api')
const dining = require('../../lib/dining-alliance')
const {money} = require('../../lib/catalog')
const {requestKey,dateLabel} = require('../../lib/orders')
const KEY = 'mall.diningPendingWithdrawal.v1'
function removeOwned(pending) {
 const saved=wx.getStorageSync(KEY)
 if(wx.getStorageSync('mall.memberToken')===pending.identity && saved?.identity===pending.identity && saved.body?.requestKey===pending.body.requestKey)wx.removeStorageSync(KEY)
}
Page({
 data:{state:'loading',error:'',restaurants:[],restaurant:null,section:'products',products:[],orders:[],account:null,amount:'',busy:false,pending:false,detail:null,page:0,total:0},
 onShow() {
  this.active=true; this.identity=wx.getStorageSync('mall.memberToken')
  const saved=wx.getStorageSync(KEY); this.pending=saved?.identity===this.identity?saved:null
  this.setData({pending:!!this.pending,busy:false,account:null,detail:null,products:[],orders:[],amount:'',...(this.pending?{section:'account'}:{})})
  return this.load()
 },
 onHide() { this.active=false; this.generation=(this.generation||0)+1; this.setData({products:[],orders:[],account:null,detail:null,amount:''}) },
 onUnload() { this.onHide() },
 valid(g,identity=this.identity) { return this.active && this.generation===g && identity===this.identity && identity===wx.getStorageSync('mall.memberToken') },
 async load() {
  const g=this.generation=(this.generation||0)+1, identity=this.identity
  this.setData({state:'loading',error:''})
  try {
   const r=await api.get('/api/v1/app/dining-alliance/restaurants')
   if(!this.valid(g,identity))return
   const restaurant=r.items.find(x=>x.id===this.pending?.restaurantId)||r.items.find(x=>x.id===this.data.restaurant?.id)||r.items[0]
   this.setData({restaurants:r.items,restaurant})
   if(!restaurant){this.setData({state:'empty'});return}
   return this.loadSection()
  }catch(e){if(this.valid(g,identity))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}
 },
 chooseRestaurant(e) { if(this.data.busy||this.pending)return;this.setData({restaurant:this.data.restaurants[Number(e.detail.value)],detail:null});return this.loadSection() },
 section(e) { if(this.data.busy||this.pending)return;this.setData({section:e.currentTarget.dataset.section,detail:null});return this.loadSection() },
 async loadSection(more=false) {
  if(!this.data.restaurant)return
  const g=this.generation=(this.generation||0)+1, identity=this.identity, section=this.data.section, restaurantId=this.data.restaurant.id
  const page=more?this.data.page+1:1
  this.setData({state:'loading',error:'',...(more?{}:{products:[],orders:[],account:null,detail:null,page:0,total:0})})
  try {
   const r=await api.get(`/api/v1/app/dining-alliance/restaurants/${restaurantId}/${section}`,section==='account'?{}:{page,pageSize:50})
   if(!this.valid(g,identity))return
   if(section==='account')this.setData({account:{...r,available:money(r.availableFen),pending:money(r.pendingFen),held:money(r.heldFen),incomes:(r.incomes||[]).map(x=>({...x,amount:money(x.commissionFen),statusLabel:({ESTIMATED:'预计佣金 · 待订单完成',PENDING:'已入账 · 待过售后期',AVAILABLE:'可提现',HELD:'冻结待处理'})[x.status]||'状态待核实'})),withdrawals:(r.withdrawals||[]).map(x=>({...x,amount:money(x.amountFen),date:dateLabel(x.createdAt)}))}})
   else {const rows=(r.items||[]).map(x=>({...x,amount:money(x.payableFen)}));this.setData({[section]:more?[...this.data[section],...rows]:rows,page:r.page||page,total:r.total??rows.length})}
   this.setData({state:'ready'})
  }catch(e){if(this.valid(g,identity))this.setData({state:'error',error:e.message})}
 },
 more(){if(this.data.state!=='loading'&&['products','orders'].includes(this.data.section)&&this.data[this.data.section].length<this.data.total)return this.loadSection(true)},
 async toggle(e) {
  if(this.data.busy)return
  const product=this.data.products.find(x=>x.id===e.currentTarget.dataset.id);if(!product)return
  const g=this.generation,identity=this.identity,restaurantId=this.data.restaurant.id
  this.setData({busy:true,error:''})
  try{await api.patch(`/api/v1/app/dining-alliance/restaurants/${restaurantId}/products`,{productId:product.id,onSale:!product.onSale,revision:product.listingRevision??product.revision});if(this.valid(g,identity)){this.setData({busy:false});await this.loadSection()}}
  catch(e){if(this.valid(g,identity))this.setData({error:e.message})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 async openOrder(e) {
  const g=this.generation,identity=this.identity
  try{const detail=await api.get(`/api/v1/app/dining-alliance/restaurants/${this.data.restaurant.id}/orders/${e.currentTarget.dataset.id}`);if(this.valid(g,identity))this.setData({detail:{...detail,amount:money(detail.payableFen)}})}
  catch(e){if(this.valid(g,identity))this.setData({error:e.message})}
 },
 closeDetail(){this.setData({detail:null})},
 amount(e){if(!this.pending)this.setData({amount:e.detail.value})},
 async withdraw() {
  if(this.data.busy)return
  if(this.identity!==wx.getStorageSync('mall.memberToken'))return
  try {
   if(!this.pending){const amountFen=dining.amountFen(this.data.amount);if(amountFen>this.data.account.availableFen)throw new Error('可提现余额不足。');const intent={identity:this.identity,restaurantId:this.data.restaurant.id,body:{amountFen,requestKey:requestKey()}};wx.setStorageSync(KEY,intent);this.pending=intent}
  }catch(e){this.setData({error:e.message});return}
  const pending={...this.pending,body:{...this.pending.body}},identity=pending.identity,g=this.generation
  this.setData({busy:true,pending:true,error:''})
  try {
   await api.post(`/api/v1/app/dining-alliance/restaurants/${pending.restaurantId}/withdrawals`,pending.body)
   removeOwned(pending)
   if(!this.valid(g,identity))return
   if(this.pending?.body.requestKey===pending.body.requestKey)this.pending=null
   this.setData({pending:false,amount:'',busy:false});await this.loadSection()
  }catch(e){const definite=e.statusCode&&e.statusCode<500&&![401,408,429].includes(e.statusCode);if(definite)removeOwned(pending);if(!this.valid(g,identity))return;if(definite){if(this.pending?.body.requestKey===pending.body.requestKey)this.pending=null;this.setData({pending:false,error:e.message})}else this.setData({error:'申请结果待确认，请重试原申请。'})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 login(){wx.navigateTo({url:'/pages/login/login?returnTo=member'})}
})
