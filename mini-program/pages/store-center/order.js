const api = require('../../lib/api')
const orders = require('../../lib/orders')
const stores = require('../../lib/stores')
const governance = require('../../lib/fulfillment-governance')
const redemption = require('../../lib/store-redemption')
const durable = require('../../lib/store-fulfillment-pending')
function presentItems(r){return (r.items||[]).filter(item=>item.fulfillmentKind==='SHIP').map(item=>({...item,specLabel:Array.isArray(item.specs)&&item.specs.length?item.specs.map(spec=>`${spec.name}：${spec.value}`).join(' · '):item.specLabel||'默认规格'}))}
function verifiedItems(r,preview){
 if(!Array.isArray(preview)||!preview.length)throw new Error('可交付商品待核实，请重新加载。');
 const source=presentItems(r),seen=new Set();
 return preview.map(row=>{const item=source.find(value=>value.orderLineId===row.orderLineId);if(!item||seen.has(row.orderLineId)||!Number.isSafeInteger(row.deliverableQuantity)||row.deliverableQuantity<1||row.deliverableQuantity>item.quantity)throw new Error('可交付数量待核实，请重新加载。');seen.add(row.orderLineId);return {...item,quantity:row.deliverableQuantity}})
}
Page({
 ...redemption,
 data:{state:'loading',error:'',actionMessage:'',order:null,items:[],delivery:null,busy:false,note:'',pickupCode:'',redemptionDrafts:{},redeemItems:[],failureReason:'',actionMessage:'',fulfillmentOwnerLabel:'',inventoryWarehouseLabel:'',canFailDelivery:false,canRetryDelivery:false,uncertain:false,needsRetryCredential:false,retryCredential:'',canPrepare:false,canDispatch:false,canComplete:false,canShip:false,verified:false,verifying:false,goodsConfirmed:false,scanning:false,manual:false},
 onLoad(o){this.storeId=o.storeId;this.orderId=o.id;this.identity=wx.getStorageSync('mall.memberToken');this.pending=durable.read(this.identity,this.storeId,this.orderId)||undefined;return this.load()},
 onHide(){this.hidden=true;this.generation=(this.generation||0)+1;this.verification=null;this.setData({busy:false,order:null,items:[],delivery:null,redeemItems:[],redemptionDrafts:{},verified:false,goodsConfirmed:false,verifying:false,pickupCode:'',retryCredential:'',note:'',actionMessage:''})},
 async onShow(){if(!this.hidden)return;this.hidden=false;if(this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({state:'error',error:'登录状态已改变，请重新打开前置仓中心。',scanning:false});return}this.setData({scanning:false});if(this.pending)this.setData({busy:false,uncertain:true,error:'上次操作结果待确认，请重试确认同一操作。'});await this.load();if(this.scanResult&&!this.disposed){const code=this.scanResult;this.scanResult=null;this.setData({pickupCode:code});return this.verifyPickup()}},
 onUnload(){this.disposed=true;this.scanSession=null;this.scanResult=null;this.onHide()},
 valid(g){return !this.disposed&&!this.hidden&&g===this.generation&&this.identity===wx.getStorageSync('mall.memberToken')},
 root(){return `/api/v1/app/store-center/stores/${encodeURIComponent(this.storeId)}/orders/${encodeURIComponent(this.orderId)}`},
 async load(){const gen=this.generation=(this.generation||0)+1;this.setData({state:'loading',error:'',actionMessage:'',order:null,items:[],redeemItems:[],redemptionDrafts:{},delivery:null});try{const r=await api.get(this.root());if(this.valid(gen)){this.present(r);if(this.pending&&durable.confirmed(r,this.pending.key)){this.clearPending();this.setData({uncertain:false,needsRetryCredential:false,actionMessage:'已确认上次操作成功，履约进度已更新。'})}else if(this.pending)this.setData({uncertain:true,needsRetryCredential:!!(this.pending.credentialField&&!this.pending.body[this.pending.credentialField]),error:'上次操作结果待确认，请重试确认同一操作。'})}}catch(e){if(this.authExpired(gen,e))return;if(this.valid(gen))this.setData({state:e.statusCode===403?'denied':'error',error:e.message})}},
 present(r){
  const d=r.storeDelivery,paid=r.status==='PAID'
  const local=['DELIVERY','PICKUP'].includes(r.deliveryMode)&&(r.fulfillmentStoreId||r.storeId)===this.storeId
  const available=r.canFulfill===true&&(!d||d.canFulfill!==false)
  this.verification=null
  this.setData({state:'ready',error:'',completedAtLabel:d&&d.completedAt?orders.dateLabel(d.completedAt):'',order:r,items:presentItems(r),delivery:d,
   redeemItems:governance.storeRedemptionItems(r,this.storeId),redemptionDrafts:{},verified:false,goodsConfirmed:false,
   fulfillmentOwnerLabel:governance.ownerLabel(r),inventoryWarehouseLabel:r.inventoryWarehouse&&r.inventoryWarehouse.name||'',failureReason:d&&d.failureReason||'',
   statusLabel:({PAID:'已付款',PENDING_PAYMENT:'待付款',CLOSED:'已关闭'})[r.status]||r.status,
   deliveryStatus:({WAITING_PREPARATION:'待备货',WAITING_PICKUP:'待自提',WAITING_DELIVERY:'待配送',IN_TRANSIT:'配送中',EXCEPTION:'配送异常',COMPLETED:r.deliveryMode==='PICKUP'?'已完成自提':'已交付',WAITING_SHIPMENT:'待平台发货',WAITING_REDEMPTION:'待核销',IN_PROGRESS:'履约中',AFTER_SALE:'售后处理中'})[d?d.status:r.fulfillmentStatus]||r.fulfillmentStatus,
   deliveryLabel:stores.MODE_LABELS[r.deliveryMode]||'权益核销',
   canPrepare:!!(paid&&local&&available&&d&&d.status==='WAITING_PREPARATION'),
   canDispatch:!!(paid&&local&&available&&r.deliveryMode==='DELIVERY'&&d&&d.status==='WAITING_DELIVERY'),
   canComplete:!!(paid&&local&&available&&d&&(d.status==='WAITING_PICKUP'||d.status==='IN_TRANSIT')),
   canFailDelivery:!!(paid&&local&&r.deliveryMode==='DELIVERY'&&d&&d.status==='IN_TRANSIT'),
   canRetryDelivery:!!(paid&&local&&available&&r.deliveryMode==='DELIVERY'&&d&&d.status==='EXCEPTION'),canShip:false})
 },
 authExpired(gen,error){
  if(!error||error.statusCode!==401||this.disposed||this.hidden||gen!==this.generation||wx.getStorageSync('mall.memberToken'))return false
  this.pending=null;this.verification=null
  this.setData({state:'auth',error:'登录已过期，请重新登录后进入前置仓中心。',order:null,items:[],redeemItems:[],redemptionDrafts:{},delivery:null,busy:false,uncertain:false,verified:false,goodsConfirmed:false})
  return true
 },
 input(e){const k=e.currentTarget.dataset.key;if(!['note','pickupCode'].includes(k)||this.pending||this.data.busy)return;if(k==='pickupCode'){this.scanSession=null;this.scanResult=null;this.previewToken=(this.previewToken||0)+1;this.verification=null;this.setData({verified:false,goodsConfirmed:false,verifying:false,scanning:false,error:'',items:this.data.order?presentItems(this.data.order):[]})}this.setData({[k]:e.detail.value})},
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
  if(!({PREPARE:this.data.canPrepare,DISPATCH:this.data.canDispatch,COMPLETE:this.data.canComplete,FAIL_DELIVERY:this.data.canFailDelivery,RETRY_DELIVERY:this.data.canRetryDelivery})[action])return;
  if(action==='FAIL_DELIVERY'&&String(this.data.note||'').trim().length<3){this.setData({error:'请填写至少 3 字配送异常原因。',actionMessage:''});return}
  if(action==='COMPLETE'&&r.deliveryMode==='DELIVERY'&&String(this.data.note||'').trim().length<5){this.setData({error:'请填写至少 5 字交付说明，再确认商品已送达。',actionMessage:''});return}
  if(action==='COMPLETE'&&r.deliveryMode==='PICKUP'){
   const v=this.verification;if(!v||v.code!==this.data.pickupCode||v.revision!==r.revision||!this.data.goodsConfirmed){this.setData({error:'请先核对自提凭证，并确认商品已交给顾客。'});return}
   const gen=this.generation;const confirm=await new Promise(resolve=>wx.showModal({title:'确认完成自提',content:'请确认商品规格、数量已核对，且已交给顾客。确认提货后自提凭证将失效。',confirmText:'确认提货',success:x=>resolve(x.confirm),fail:()=>resolve(false)}));
   if(!confirm||!this.valid(gen)||this.pending||this.verification!==v||!this.data.goodsConfirmed)return;
  }
  const body={action,expectedRevision:r.revision,note:this.data.note};if(action==='COMPLETE'&&r.deliveryMode==='PICKUP')body.pickupCode=this.verification.code;this.pending={key:orders.requestKey(),body};return this.send()
 },
 retryCredentialInput(e){if(this.pending&&!this.data.busy)this.setData({retryCredential:e.detail.value,error:'上次操作结果待确认，请重试确认同一操作。'})},
 clearPending(){if(this.pending)durable.clear(this.identity,this.storeId,this.orderId,this.pending.key);this.pending=null},
 async send(){
  if(!this.pending||this.data.busy||this.hidden)return;
  if(this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({error:'登录状态已改变，请重新打开前置仓中心。'});return}
  if(this.data.uncertain){this.setData({busy:true});await this.load();if(this.hidden||this.disposed)return;this.setData({busy:false});if(!this.pending||this.data.state!=='ready')return}
  const intent=this.pending;
  if(intent.credentialField&&!intent.body[intent.credentialField]){const code=String(this.data.retryCredential||'').trim();if(!code||durable.fingerprint(code)!==intent.credentialFingerprint){this.setData({uncertain:true,needsRetryCredential:true,error:'请重新输入上次使用的同一凭证，才能确认原操作。'});return}this.pending={...intent,body:{...intent.body,[intent.credentialField]:code}}}
  try{durable.save(this.identity,this.storeId,this.orderId,this.pending)}catch(e){this.setData({uncertain:true,error:'无法保存操作恢复信息，请释放本地存储后重试同一操作。'});return}
  this.setData({busy:true,error:'',actionMessage:''});const gen=this.generation,pending=this.pending;
  try{const r=await api.post(`${this.root()}/${pending.endpoint||'fulfillment'}`,pending.body,{'Idempotency-Key':pending.key});if(!this.valid(gen))return;this.clearPending();this.present(r);this.setData({needsRetryCredential:false,retryCredential:'',uncertain:false,pickupCode:'',note:'',actionMessage:pending.endpoint==='redemptions'?'权益核销成功，已同步剩余次数。':'操作成功，已更新履约进度。'})}catch(e){if(this.authExpired(gen,e))return;if(!this.valid(gen))return;if(e.statusCode&&e.statusCode<500&&![408,429].includes(e.statusCode)){this.clearPending();this.verification=null;this.setData({needsRetryCredential:false,retryCredential:'',uncertain:false,verified:false,goodsConfirmed:false,error:e.message})}else this.setData({uncertain:true,error:'操作结果尚未确认，请使用同一请求重试。'})}finally{if(this.valid(gen))this.setData({busy:false})}
 },
 login(){wx.navigateTo({url:'/pages/login/login?returnTo=member'})},
})
