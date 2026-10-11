const api = require('../../lib/api')
const orders = require('../../lib/orders')
const stores = require('../../lib/stores')
function presentItems(r){return (r.items||[]).filter(item=>r.deliveryMode!=='PICKUP'||item.fulfillmentKind==='SHIP').map(item=>({...item,specLabel:Array.isArray(item.specs)&&item.specs.length?item.specs.map(spec=>`${spec.name}：${spec.value}`).join(' · '):item.specLabel||'默认规格'}))}
function verifiedItems(r,preview){
 if(!Array.isArray(preview)||!preview.length)throw new Error('可交付商品待核实，请重新加载。');
 const source=presentItems(r),seen=new Set();
 return preview.map(row=>{const item=source.find(value=>value.orderLineId===row.orderLineId);if(!item||seen.has(row.orderLineId)||!Number.isSafeInteger(row.deliverableQuantity)||row.deliverableQuantity<1||row.deliverableQuantity>item.quantity)throw new Error('可交付数量待核实，请重新加载。');seen.add(row.orderLineId);return {...item,quantity:row.deliverableQuantity}})
}
Page({
 data:{state:'loading',error:'',order:null,items:[],delivery:null,busy:false,note:'',pickupCode:'',carrierCode:'',trackingNo:'',uncertain:false,canPrepare:false,canDispatch:false,canComplete:false,canShip:false,verified:false,verifying:false,goodsConfirmed:false,scanning:false,manual:false},
 onLoad(o){this.storeId=o.storeId;this.orderId=o.id;this.identity=wx.getStorageSync('mall.memberToken');return this.load()},
 onHide(){this.hidden=true;this.generation=(this.generation||0)+1;this.verification=null;this.setData({order:null,items:[],delivery:null,verified:false,goodsConfirmed:false,verifying:false,pickupCode:'',note:''})},
 async onShow(){if(!this.hidden)return;this.hidden=false;if(this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({state:'error',error:'登录状态已改变，请重新打开前置仓中心。',scanning:false});return}this.setData({scanning:false});if(this.pending)this.setData({busy:false,uncertain:true,error:'上次操作结果待确认，请重试确认同一操作。'});await this.load();if(this.scanResult&&!this.disposed){const code=this.scanResult;this.scanResult=null;this.setData({pickupCode:code});return this.verifyPickup()}},
 onUnload(){this.disposed=true;this.scanSession=null;this.scanResult=null;this.onHide()},
 valid(g){return !this.disposed&&!this.hidden&&g===this.generation&&this.identity===wx.getStorageSync('mall.memberToken')},
 root(){return `/api/v1/app/store-center/stores/${encodeURIComponent(this.storeId)}/orders/${encodeURIComponent(this.orderId)}`},
 async load(){const gen=this.generation=(this.generation||0)+1;this.setData({state:'loading'});try{const r=await api.get(this.root());if(this.valid(gen))this.present(r)}catch(e){if(this.valid(gen))this.setData({state:'error',error:e.message})}},
 present(r){const d=r.storeDelivery;const paid=r.status==='PAID';this.verification=null;this.setData({state:'ready',completedAtLabel:d&&d.completedAt?orders.dateLabel(d.completedAt):'',order:r,items:presentItems(r),delivery:d,verified:false,goodsConfirmed:false,statusLabel:({PAID:'已付款',PENDING_PAYMENT:'待付款',CLOSED:'已关闭'})[r.status]||r.status,deliveryStatus:({WAITING_PREPARATION:'待备货',WAITING_PICKUP:'待自提',WAITING_DELIVERY:'待配送',IN_TRANSIT:'配送中',COMPLETED:r.deliveryMode==='PICKUP'?'已完成自提':'已交付',WAITING_SHIPMENT:'待发货',AFTER_SALE:'售后处理中'})[d?d.status:r.fulfillmentStatus]||r.fulfillmentStatus,deliveryLabel:stores.MODE_LABELS[r.deliveryMode]||r.deliveryMode,canPrepare:paid&&d&&d.status==='WAITING_PREPARATION',canDispatch:paid&&r.deliveryMode==='DELIVERY'&&d&&d.status==='WAITING_DELIVERY',canComplete:paid&&d&&(d.status==='WAITING_PICKUP'||d.status==='IN_TRANSIT'),canShip:paid&&r.deliveryMode==='EXPRESS'&&!r.shipment})},
 input(e){const k=e.currentTarget.dataset.key;if(!['note','pickupCode','carrierCode','trackingNo'].includes(k)||this.pending||this.data.busy)return;if(k==='pickupCode'){this.scanSession=null;this.scanResult=null;this.previewToken=(this.previewToken||0)+1;this.verification=null;this.setData({verified:false,goodsConfirmed:false,verifying:false,scanning:false,error:'',items:this.data.order?presentItems(this.data.order):[]})}this.setData({[k]:e.detail.value})},
 showManual(){if(!this.pending&&!this.data.busy)this.setData({manual:true})},
 confirmGoods(e){this.setData({goodsConfirmed:this.data.verified&&e.detail.value.includes('checked')})},
 scanPickup(){
  if(this.data.busy||this.data.verifying||this.data.scanning||this.pending||!this.data.canComplete)return;
  if(!wx.scanCode){this.setData({manual:true,error:'当前设备无法扫码，请输入顾客凭证。'});return}
  const session={identity:this.identity};this.scanSession=session;this.verification=null;this.setData({scanning:true,verified:false,goodsConfirmed:false,error:''});
  wx.scanCode({onlyFromCamera:true,scanType:['qrCode'],success:r=>{if(this.disposed||this.scanSession!==session||session.identity!==wx.getStorageSync('mall.memberToken'))return;this.scanSession=null;const code=String(r.result||'').trim();if(this.hidden||!this.data.order){this.scanResult=code;return}this.setData({scanning:false,pickupCode:code});this.verifyPickup()},fail:e=>{if(this.disposed||this.scanSession!==session||session.identity!==wx.getStorageSync('mall.memberToken'))return;this.scanSession=null;this.setData({scanning:false,manual:!String(e.errMsg||'').includes('cancel'),error:String(e.errMsg||'').includes('cancel')?'':'无法使用相机，请输入顾客凭证。'})}})
 },
 async verifyPickup(){
  if(this.data.busy||this.pending||!this.data.order||!this.data.canComplete||this.hidden)return;
  const code=String(this.data.pickupCode||'').trim();if(!code||code.length>200){this.setData({error:'请输入有效的自提凭证。'});return}
  this.scanSession=null;const gen=this.generation, token=this.previewToken=(this.previewToken||0)+1,revision=this.data.order.revision;
  this.verification=null;this.setData({verifying:true,verified:false,goodsConfirmed:false,error:''});
  try{const r=await api.post(`${this.root()}/pickup-preview`,{pickupCode:code});if(!this.valid(gen)||token!==this.previewToken)return;if(!r.verified||r.orderId!==this.orderId||r.revision!==revision)throw new Error('订单已变化，请重新加载后核对凭证。');const items=verifiedItems(this.data.order,r.items);this.verification={code,revision};this.setData({verified:true,pickupCode:code,items})}catch(e){if(this.valid(gen)&&token===this.previewToken)this.setData({error:e.message})}finally{if(this.valid(gen)&&token===this.previewToken)this.setData({verifying:false})}
 },
 async act(e){
  if(this.data.busy||this.data.verifying||!this.data.order||this.hidden)return;
  if(this.pending){this.setData({error:'上次操作结果待确认，请先重试同一操作。'});return}
  const action=e.currentTarget.dataset.action,r=this.data.order;
  if(!({PREPARE:this.data.canPrepare,DISPATCH:this.data.canDispatch,COMPLETE:this.data.canComplete,SHIP:this.data.canShip})[action])return;
  if(action==='COMPLETE'&&r.deliveryMode==='PICKUP'){
   const v=this.verification;if(!v||v.code!==this.data.pickupCode||v.revision!==r.revision||!this.data.goodsConfirmed){this.setData({error:'请先核对自提凭证，并确认商品已交给顾客。'});return}
   const gen=this.generation;const confirm=await new Promise(resolve=>wx.showModal({title:'确认完成自提',content:'请确认商品规格、数量已核对，且已交给顾客。核销成功后凭证将失效。',confirmText:'确认核销',success:x=>resolve(x.confirm),fail:()=>resolve(false)}));
   if(!confirm||!this.valid(gen)||this.pending||this.verification!==v||!this.data.goodsConfirmed)return;
  }
  const body={action,expectedRevision:r.revision,note:this.data.note};if(action==='COMPLETE'&&r.deliveryMode==='PICKUP')body.pickupCode=this.verification.code;if(action==='SHIP'){body.carrierCode=this.data.carrierCode;body.trackingNo=this.data.trackingNo}this.pending={key:orders.requestKey(),body};return this.send()
 },
 async send(){
  if(!this.pending||this.data.busy||this.hidden)return;
  if(this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({error:'登录状态已改变，请重新打开前置仓中心。'});return}
  this.setData({busy:true,error:''});const gen=this.generation,pending=this.pending;
  try{const r=await api.post(`${this.root()}/fulfillment`,pending.body,{'Idempotency-Key':pending.key});if(!this.valid(gen))return;this.pending=null;this.present(r);this.setData({uncertain:false,pickupCode:'',note:''})}catch(e){if(!this.valid(gen))return;if(e.statusCode&&e.statusCode<500&&![408,429].includes(e.statusCode)){this.pending=null;this.verification=null;this.setData({uncertain:false,verified:false,goodsConfirmed:false,error:e.message})}else this.setData({uncertain:true,error:'操作结果尚未确认，请使用同一请求重试。'})}finally{if(this.valid(gen))this.setData({busy:false})}
 },
})
