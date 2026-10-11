const api = require('../../lib/api')
const stores = require('../../lib/stores')
const { money } = require('../../lib/catalog')
const finance = require('../../lib/store-finance')
const dashboard = require('../../lib/store-dashboard')
const { requestKey } = require('../../lib/orders')
const emptyWithdrawal = () => ({amount:'',payeeName:'',bankName:'',bankAccount:''})
Page({
 data:{state:'loading',error:'',stores:[],store:null,section:'home',products:[],orders:[],aftersales:[],account:null,drafts:{},busy:false,page:0,total:0,keyword:'',canProducts:false,canOrders:false,canAccounts:false,withdrawalForm:emptyWithdrawal(),withdrawalUncertain:false,overview:null,accountError:'',accountPanel:'summary',orderFilter:'',activeAftersales:false,orderFilters:dashboard.ORDER_FILTERS,orderEmpty:'本店暂无订单。'},
 onShow(){this.disposed=false;this.hidden=false;if(this.identity&&this.identity!==wx.getStorageSync('mall.memberToken'))this.clearFinance();return this.loadStores()},
 onHide(){this.hidden=true;this.generation=(this.generation||0)+1;this.setData({products:[],orders:[],aftersales:[],account:null,overview:null,accountError:'',withdrawalForm:emptyWithdrawal()})},
 onUnload(){this.disposed=true;this.onHide();this.clearFinance()},
 async loadStores(){const gen=this.generation=(this.generation||0)+1;this.identity=wx.getStorageSync('mall.memberToken');this.setData({state:'loading',error:'',stores:[],store:null,overview:null,account:null,accountError:'',...(!this.pendingWithdrawal?{busy:false}:{})});if(!this.identity){this.setData({state:'auth',error:'请登录后进入前置仓中心。'});return}try{const r=await api.get('/api/v1/app/store-center/stores');if(!this.valid(gen))return;this.setData({stores:r.items});const store=r.items.find(s=>s.id===stores.managed())||r.items[0];if(!store){this.setData({state:'empty'});return}this.choose(store);return this.loadSection()}catch(e){if(this.authExpired(gen,e))return;if(this.valid(gen))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}},
 authExpired(gen,error){
  if(!error||error.statusCode!==401||this.disposed||this.hidden||gen!==this.generation||!this.identity||wx.getStorageSync('mall.memberToken'))return false
  this.clearFinance();this.setData({state:'auth',error:'登录已过期，请重新登录后进入前置仓中心。',stores:[],store:null,products:[],orders:[],aftersales:[],overview:null,accountError:'',canProducts:false,canOrders:false,canAccounts:false});return true
 },
 valid(gen){return !this.disposed&&gen===this.generation&&this.identity===wx.getStorageSync('mall.memberToken')},
 choose(store){stores.manage(store.id);const p=store.permissions||[];const section=this.data.section;const required=section==='products'?'products':section==='account'?'accounts':['orders','aftersales'].includes(section)?'orders':'';const reset=required&&!p.includes(required)&&!this.pendingWithdrawal;this.setData({store,canProducts:p.includes('products'),canOrders:p.includes('orders'),canAccounts:p.includes('accounts'),...(reset?{section:'home',accountPanel:'summary',orderFilter:'',activeAftersales:false}:{})})},
 selectStore(e){if(this.data.busy||this.pendingWithdrawal){this.setData({error:'提现结果待确认，请先重试原申请。'});return}const store=this.data.stores[Number(e.detail.value)];if(!store)return;this.choose(store);this.setData({section:'home',orderFilter:'',activeAftersales:false,accountPanel:'summary'});return this.loadSection()},
 async loadSection(more=false){if(!this.data.store)return;const gen=this.generation=(this.generation||0)+1;const section=this.data.section;const page=more?this.data.page+1:1;this.setData({state:'loading',error:'',...(more?{}:{products:[],orders:[],aftersales:[],account:null,drafts:{}})});try{const root=`/api/v1/app/store-center/stores/${encodeURIComponent(this.data.store.id)}`;if(section==='home'){return await this.loadOverview(root,gen)}const r=await api.get(`${root}/${section==='products'?'products':section==='orders'?'orders':section==='aftersales'?'aftersales':'account'}`,dashboard.query(section,page,this.data));if(!this.valid(gen))return;if(section==='account'){this.setData({account:finance.present(r),state:'ready'});return}if(section==='products'){const rows=(r.items||[]).map(p=>({...p,platformStatusLabel:({ON_SALE:'上架',OFF_SALE:'下架',ACTIVE:'上架',INACTIVE:'下架'})[p.platformStatus]||p.platformStatus,pools:stores.stockPools(p)}));this.setData({products:more?[...this.data.products,...rows]:rows,total:r.total,page,state:'ready'})}else{const rows=(r.items||r.rows||[]).map(o=>({...o,amount:money(o.payableFen),deliveryLabel:stores.MODE_LABELS[o.deliveryMode]||'',statusLabel:section==='orders'?dashboard.orderStatus(o):({PENDING_REVIEW:'待审核',WAITING_RETURN:'待退货',WAITING_REFUND:'待退款',COMPLETED:'已结束'})[o.status]||o.status}));this.setData({[section]:more?[...this.data[section],...rows]:rows,total:r.total??rows.length,page,state:'ready'})}}catch(e){if(this.authExpired(gen,e))return;if(this.valid(gen))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}},
 async loadOverview(root,gen){
  const results=await Promise.allSettled([api.get(`${root}/overview`),...(this.data.canAccounts?[api.get(`${root}/account`)]:[])])
  const failed=results.find(result=>result.status==='rejected'&&result.reason.statusCode===401)
  if(failed&&this.authExpired(gen,failed.reason))return
  if(!this.valid(gen))return
  if(results[0].status==='rejected')throw results[0].reason
  const permissions=Array.isArray(results[0].value.permissions)?results[0].value.permissions:this.data.store.permissions||[];const store={...this.data.store,permissions};this.choose(store);const balance=permissions.includes('accounts')?results[1]:null;this.setData({overview:dashboard.present(results[0].value,permissions),account:balance&&balance.status==='fulfilled'?finance.present(balance.value):null,accountError:balance&&balance.status==='rejected'?balance.reason.message:'',state:'ready'})
 },
 accountPanel(e){if(this.data.busy||this.pendingWithdrawal){this.setData({error:'提现结果待确认，请先重试原申请。'});return}const panel=e.currentTarget.dataset.panel;if(['summary','income','withdraw','history'].includes(panel))this.setData({accountPanel:panel})},
 async refresh(){return this.data.store?this.loadSection():this.loadStores()},
 section(e){if(this.data.busy||this.pendingWithdrawal){this.setData({error:'提现结果待确认，请先重试原申请。'});return}const d=e.currentTarget.dataset;const section=d.section;if(!['home','products','orders','aftersales','account'].includes(section))return;const allowed=section==='home'||(section==='products'?this.data.canProducts:section==='account'?this.data.canAccounts:this.data.canOrders);if(!allowed){this.setData({error:'当前账号没有此功能权限。'});return}this.setData({section,keyword:'',orderFilter:section==='orders'?d.fulfillment||'':'',activeAftersales:section==='aftersales'&&!!d.active,accountPanel:d.accountPanel||'summary',orderEmpty:dashboard.emptyOrders(d.fulfillment||'')});return this.loadSection()},
 keyword(e){this.setData({keyword:e.detail.value})},
 more(){if(this.data[this.data.section]&&this.data[this.data.section].length<this.data.total)return this.loadSection(true)},
 stockInput(e){const {product,pool}=e.currentTarget.dataset;this.setData({drafts:{...this.data.drafts,[product]:{...(this.data.drafts[product]||{}),[pool]:e.detail.value}}})},
 async saveProduct(e){
  if(this.data.busy||!this.data.store)return
  const p=this.data.products.find(p=>p.id===e.currentTarget.dataset.id);if(!p)return
  const gen=this.generation,storeId=this.data.store.id,identity=this.identity
  const current=()=>!this.hidden&&!this.disposed&&this.valid(gen)&&this.data.store&&this.data.store.id===storeId&&identity===wx.getStorageSync('mall.memberToken')
  try{
   const onSale=e.currentTarget.dataset.toggle?p.onSale===false:p.onSale
   const body=stores.stockPatch(p,this.data.drafts[p.id]||{},onSale)
   this.setData({busy:true,error:''})
   await api.patch(`/api/v1/app/store-center/stores/${encodeURIComponent(storeId)}/products/${encodeURIComponent(p.id)}`,body)
   if(!current())return
   this.setData({busy:false})
   const reload=this.loadSection(),reloadGen=this.generation;await reload
   if(!this.hidden&&!this.disposed&&this.valid(reloadGen)&&this.data.store&&this.data.store.id===storeId&&this.data.state==='ready')wx.showToast({title:'已保存并重新核对',icon:'success'})
  }catch(e){if(this.authExpired(gen,e))return;if(current())this.setData({error:e.statusCode===409?'库存或销售状态已改变，请重新加载核对后再填写。':e.message})}
  finally{if(current())this.setData({busy:false})}
 },
 clearFinance(){this.pendingWithdrawal=null;this.setData({account:null,withdrawalForm:emptyWithdrawal(),withdrawalUncertain:false,busy:false})},
 withdrawalInput(e){const key=e.currentTarget.dataset.key;if(['amount','payeeName','bankName','bankAccount'].includes(key)&&!this.data.busy&&!this.pendingWithdrawal)this.setData({withdrawalForm:{...this.data.withdrawalForm,[key]:e.detail.value}})},
 async requestWithdrawal(){
  if(this.data.busy||!this.data.canAccounts||!this.data.account||!this.data.account.withdrawalReady)return
  if(this.pendingWithdrawal)return this.sendWithdrawal()
  try{const body=finance.withdrawal(this.data.withdrawalForm,this.data.account.balance&&this.data.account.balance.availableFen,requestKey());this.pendingWithdrawal={storeId:this.data.store.id,identity:this.identity,body};return this.sendWithdrawal()}catch(e){this.setData({error:e.message})}
 },
 async sendWithdrawal(){
  const pending=this.pendingWithdrawal;if(!pending||this.data.busy)return
  if(pending.identity!==wx.getStorageSync('mall.memberToken')){this.clearFinance();this.setData({state:'auth',error:'登录状态已改变，请重新登录。'});return}
  this.setData({busy:true,error:''});
  try{
   await api.post(`/api/v1/app/store-center/stores/${encodeURIComponent(pending.storeId)}/withdrawals`,pending.body)
   if(this.disposed)return
   if(pending.identity!==wx.getStorageSync('mall.memberToken')){this.clearFinance();return}
   this.pendingWithdrawal=null;this.setData({withdrawalUncertain:false,withdrawalForm:emptyWithdrawal()})
   if(!this.disposed&&!this.hidden){wx.showToast({title:'提现申请已提交',icon:'success'});await this.loadSection()}
  }catch(e){
   if(this.disposed)return
   if(pending.identity!==wx.getStorageSync('mall.memberToken')){this.clearFinance();return}
   if(e.statusCode&&e.statusCode<500&&![408,429].includes(e.statusCode)){this.pendingWithdrawal=null;this.setData({withdrawalUncertain:false,error:e.message})}
   else this.setData({withdrawalUncertain:true,withdrawalForm:emptyWithdrawal(),error:'申请结果尚未确认，请重试原申请，避免重复提交。'})
  }finally{if(!this.disposed)this.setData({busy:false})}
 },
 openAftersale(e){wx.navigateTo({url:`/pages/store-center/aftersale?storeId=${encodeURIComponent(this.data.store.id)}&id=${encodeURIComponent(e.currentTarget.dataset.id)}`})},
 openOrder(e){wx.navigateTo({url:`/pages/store-center/order?storeId=${encodeURIComponent(this.data.store.id)}&id=${encodeURIComponent(e.currentTarget.dataset.id)}`})},
 login(){wx.navigateTo({url:'/pages/login/login?returnTo=member'})},
})
