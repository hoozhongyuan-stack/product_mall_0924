const api = require('../../lib/api')
const dining = require('../../lib/dining-alliance')
const view = require('../../lib/dining-center')
const {requestKey} = require('../../lib/orders')
const KEY = 'mall.diningPendingWithdrawal.v1'
function removeOwned(pending) {
 const saved=wx.getStorageSync(KEY)
 if(wx.getStorageSync('mall.memberToken')===pending.identity && saved?.identity===pending.identity && saved.body?.requestKey===pending.body.requestKey)wx.removeStorageSync(KEY)
}
Page({
 data:{state:'loading',error:'',restaurants:[],restaurant:null,section:'products',products:[],orders:[],account:null,amount:'',busy:false,pending:false,detail:null,page:0,total:0,sections:[],accountPanel:'overview',detailLoading:false,detailError:'',detailId:''},
 onShow() {
  const identity=wx.getStorageSync('mall.memberToken'), changedIdentity=this.identity!==identity
  this.active=true; this.identity=identity
  const saved=wx.getStorageSync(KEY); this.pending=saved?.identity===this.identity?saved:null
  this.setData({...(changedIdentity?{restaurant:null,restaurants:[],sections:[],section:'products',accountPanel:'overview'}:{}),pending:!!this.pending,busy:false,account:null,detail:null,products:[],orders:[],amount:'',...(this.pending?{section:'account',accountPanel:'overview'}:{})})
  return this.load()
 },
 onHide() { this.detailGeneration=(this.detailGeneration||0)+1;this.active=false; this.generation=(this.generation||0)+1; this.setData({products:[],orders:[],account:null,detail:null,detailId:'',detailError:'',detailLoading:false,amount:''}) },
 onUnload() { this.onHide() },
 valid(g,identity=this.identity) { return this.active && this.generation===g && identity===this.identity && identity===wx.getStorageSync('mall.memberToken') },
 async load() {
  const g=this.generation=(this.generation||0)+1, identity=this.identity
  this.setData({state:'loading',error:''})
  try {
   const r=await api.get('/api/v1/app/dining-alliance/restaurants')
   if(!this.valid(g,identity))return
   const restaurant=r.items.find(x=>x.id===this.pending?.restaurantId)||r.items.find(x=>x.id===this.data.restaurant?.id)||r.items[0]
   const sections=view.sections(restaurant)
   const section=this.pending?'account':sections.some(s=>s.value===this.data.section)?this.data.section:sections[0]?.value||''
   this.setData({restaurants:r.items,restaurant,sections,section})
   if(!restaurant){this.setData({state:'empty'});return}
   return this.loadSection()
  }catch(e){if(this.valid(g,identity))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}
 },
 chooseRestaurant(e) { if(this.data.busy||this.pending)return;const restaurant=this.data.restaurants[Number(e.detail.value)];if(!restaurant)return;const sections=view.sections(restaurant);this.detailGeneration=(this.detailGeneration||0)+1;this.setData({restaurant,sections,section:sections.some(s=>s.value===this.data.section)?this.data.section:sections[0]?.value||'',detail:null,detailId:'',detailError:''});return this.loadSection() },
 section(e) { if(this.data.busy||this.pending)return;const section=e.currentTarget.dataset.section;if(!view.sections(this.data.restaurant).some(s=>s.value===section))return;this.detailGeneration=(this.detailGeneration||0)+1;this.setData({section,detail:null,detailId:'',detailError:''});return this.loadSection() },
 async loadSection(more=false) {
  if(!this.data.restaurant||!this.data.section)return
  const g=this.generation=(this.generation||0)+1, identity=this.identity, section=this.data.section, restaurantId=this.data.restaurant.id
  const page=more?this.data.page+1:1
  this.setData({state:'loading',error:'',...(more?{}:{products:[],orders:[],account:null,detail:null,page:0,total:0,detailLoading:false,detailError:'',detailId:''})})
  try {
   const r=await api.get(`/api/v1/app/dining-alliance/restaurants/${restaurantId}/${section}`,section==='account'?{}:{page,pageSize:50})
   if(!this.valid(g,identity))return
   if(section==='account')this.setData({account:view.account(r)})
   else {let baseUrl='';try{baseUrl=api.baseUrl()}catch{}const rows=(r.items||[]).map(x=>section==='products'?view.product(x,baseUrl):view.order(x));this.setData({[section]:more?[...this.data[section],...rows]:rows,page:r.page||page,total:r.total??rows.length})}
   this.setData({state:'ready'})
  }catch(e){if(this.valid(g,identity))this.setData({state:'error',error:e.message})}
 },
 more(){if(this.data.state!=='loading'&&['products','orders'].includes(this.data.section)&&this.data[this.data.section].length<this.data.total)return this.loadSection(true)},
 async toggle(e) {
  if(this.data.busy||this.pending||!view.sections(this.data.restaurant).some(s=>s.value==='products'))return
  const product=this.data.products.find(x=>x.id===e.currentTarget.dataset.id);if(!product||(!product.onSale&&!product.canPutOnSale))return
  const g=this.generation,identity=this.identity,restaurantId=this.data.restaurant.id
  this.setData({busy:true,error:''})
  try{await api.patch(`/api/v1/app/dining-alliance/restaurants/${restaurantId}/products`,{productId:product.id,onSale:!product.onSale,revision:product.listingRevision??product.revision});if(this.valid(g,identity)){this.setData({busy:false});await this.loadSection()}}
  catch(e){if(this.valid(g,identity))this.setData({error:e.message})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 async openOrder(e) {
  if(!this.data.restaurant||!view.sections(this.data.restaurant).some(s=>s.value==='orders'))return
  const id=e.currentTarget.dataset.id||this.data.detailId;if(!id)return
  const g=this.generation,identity=this.identity,read=this.detailGeneration=(this.detailGeneration||0)+1
  this.setData({detail:null,detailLoading:true,detailError:'',detailId:id})
  try{const detail=await api.get(`/api/v1/app/dining-alliance/restaurants/${this.data.restaurant.id}/orders/${id}`);if(this.valid(g,identity)&&read===this.detailGeneration)this.setData({detail:view.order(detail)})}
  catch(e){if(this.valid(g,identity)&&read===this.detailGeneration)this.setData({detailError:e.message})}
  finally{if(this.valid(g,identity)&&read===this.detailGeneration)this.setData({detailLoading:false})}
 },
 closeDetail(){this.detailGeneration=(this.detailGeneration||0)+1;this.setData({detail:null,detailId:'',detailLoading:false,detailError:''})},
 imageError(e){const id=e.currentTarget.dataset.id;this.setData({products:this.data.products.map(p=>p.id===id?{...p,imageUrl:''}:p)})},
 financePanel(e){if(this.data.busy||this.pending)return;const panel=e.currentTarget.dataset.panel;if(['overview','income','withdrawals'].includes(panel))this.setData({accountPanel:panel})},
 amount(e){if(!this.pending)this.setData({amount:e.detail.value})},
 async withdraw() {
  if(this.data.busy||!this.data.restaurant||!this.pending&&(!this.data.account||!view.sections(this.data.restaurant).some(s=>s.value==='account')))return
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
   this.setData({pending:false,amount:'',busy:false,accountPanel:'withdrawals'});await this.loadSection()
  }catch(e){const definite=e.statusCode&&e.statusCode<500&&![401,408,429].includes(e.statusCode);if(definite)removeOwned(pending);if(!this.valid(g,identity))return;if(definite){if(this.pending?.body.requestKey===pending.body.requestKey)this.pending=null;this.setData({pending:false,error:e.message})}else this.setData({error:'申请结果待确认，请重试原申请。'})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 login(){wx.navigateTo({url:'/pages/login/login?returnTo=member'})}
})
