const api = require('../../lib/api')
const dining = require('../../lib/dining-alliance')
const {money} = require('../../lib/catalog')
function specLabel(sku) { return typeof sku.specLabel==='string'?sku.specLabel:Array.isArray(sku.specs)?sku.specs.map(x=>`${x.name}：${x.value}`).join(' · ')||'默认规格':typeof sku.specs==='string'?sku.specs:'默认规格' }
Page({
 data:{state:'loading',error:'',visit:null,products:[],quantities:{},count:0,busy:false,page:0,total:0},
 onLoad(options) { this.active=true;try{this.code=decodeURIComponent(options.scene||options.tableCode||'')}catch(_){this.code=''};return this.enter() },
 onShow() {
  this.active=true
  if(this.hidden){this.hidden=false;this.setData({busy:false});if(!dining.current())this.setData({state:'expired',visit:null,products:[],quantities:{},count:0,error:'请重新扫描餐桌码进入店铺。'})}
  if(this.loaded&&this.identity!==wx.getStorageSync('mall.memberToken'))dining.close()
  if(this.loaded&&!dining.current())this.setData({state:'expired',visit:null,products:[],quantities:{},count:0,error:'请重新扫描餐桌码进入店铺。'})
 },
 onHide() { this.active=false;this.hidden=true;this.generation=(this.generation||0)+1;if(!this.internal)dining.close();this.internal=false },
 onUnload() { this.active=false;this.generation=(this.generation||0)+1;dining.close() },
 valid(g,identity) { return this.active&&g===this.generation&&identity===wx.getStorageSync('mall.memberToken') },
 async enter() {
  if(this.data.busy)return
  if(!this.code){this.setData({state:'expired',error:'仅限扫描餐桌码访问。'});return}
  if(!wx.getStorageSync('mall.memberToken')){this.setData({state:'auth',error:'请先登录，再重新扫描餐桌码。'});return}
  const identity=this.identity=wx.getStorageSync('mall.memberToken'),g=this.generation=(this.generation||0)+1
  this.setData({busy:true,state:'loading',error:'',visit:null,quantities:{},count:0,products:[],page:0,total:0})
  try {
   await dining.close()
   const visit=await api.post('/api/v1/app/dining-alliance/scan',{tableCode:this.code})
   if(!this.valid(g,identity)){await api.remove(`/api/v1/app/dining-alliance/sessions/${visit.sessionId}`);return}
   dining.open(visit);this.loaded=true;this.setData({visit,busy:false});await this.refresh()
  }catch(e){if(this.valid(g,identity))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 async refresh(more=false) {
  const visit=dining.current();if(!visit)return
  const identity=wx.getStorageSync('mall.memberToken'),g=this.generation=(this.generation||0)+1,page=more?this.data.page+1:1
  this.setData({state:'loading',error:''})
  try {
   const r=await api.get(`/api/v1/app/dining-alliance/sessions/${visit.sessionId}/products`,{page,pageSize:50})
   if(!this.valid(g,identity)||dining.current()?.sessionId!==visit.sessionId)return
   const rows=(r.items||[]).map(p=>({...p,skus:(p.skus||[]).map(s=>({...s,price:money(s.unitPriceFen),specLabel:specLabel(s)}))}))
   this.setData({state:'ready',products:more?[...this.data.products,...rows]:rows,page:r.page||page,total:r.total??rows.length})
  }catch(e){if(this.valid(g,identity))this.setData({state:'error',error:e.message})}
 },
 more(){if(this.data.state!=='loading'&&this.data.products.length<this.data.total)return this.refresh(true)},
 quantity(e) {
  if(!dining.current()||this.data.state!=='ready')return
  const id=e.currentTarget.dataset.id,sku=this.data.products.flatMap(p=>p.skus).find(s=>s.id===id),delta=Number(e.currentTarget.dataset.delta)
  const quantity=(this.data.quantities[id]||0)+delta
  if(!sku||![-1,1].includes(delta)||quantity<0||quantity>sku.availableQuantity)return
  dining.select({skuId:id,quantity,seenPriceFen:sku.unitPriceFen})
  const quantities={...this.data.quantities,[id]:quantity};this.setData({quantities,count:Object.values(quantities).reduce((a,b)=>a+b,0)})
 },
 checkout(){if(!this.data.count||!dining.current())return;this.internal=true;wx.navigateTo({url:'/pages/dining/checkout',fail:()=>{this.internal=false;this.setData({error:'结算页打开失败，请重试。'})}})},
 login(){wx.navigateTo({url:'/pages/login/login?returnTo=member'})},
 scan(){wx.scanCode({onlyFromCamera:true,success:r=>{const match=(r.path||'').match(/[?&]scene=([^&]+)/);if(!match){this.setData({error:'请扫描本小程序的餐桌码。'});return}try{this.code=decodeURIComponent(match[1]);this.active=true;this.enter()}catch(_){this.setData({error:'餐桌码格式无效。'})}}})}
})
