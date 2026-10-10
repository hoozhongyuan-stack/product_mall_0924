const api=require('../../lib/api')
const {requestKey}=require('../../lib/orders')
Page({
 data:{state:'loading',error:'',case:null,note:'',receivedQuantity:'',salableQuantity:'',busy:false,uncertain:false},
 onLoad(o){this.storeId=o.storeId;this.caseId=o.id;this.identity=wx.getStorageSync('mall.memberToken');return this.load()},
 onHide(){this.disposed=true;this.setData({case:null})},
 onShow(){if(this.disposed){this.disposed=false;return this.load()}},
 root(){return `/api/v1/app/store-center/stores/${encodeURIComponent(this.storeId)}/aftersales/${encodeURIComponent(this.caseId)}`},
 async load(){this.setData({state:'loading',error:''});try{const r=await api.get(this.root());if(!this.disposed&&this.identity===wx.getStorageSync('mall.memberToken'))this.setData({state:'ready',case:r})}catch(e){this.setData({state:'error',error:e.message})}},
 input(e){const k=e.currentTarget.dataset.key;if(['note','receivedQuantity','salableQuantity'].includes(k))this.setData({[k]:e.detail.value})},
 async record(e){if(this.data.busy||this.pending||!this.data.case)return;const kind=e.currentTarget.dataset.kind;const body={kind,note:this.data.note,expectedRevision:this.data.case.revision};if(kind==='RECEIPT'){for(const k of ['receivedQuantity','salableQuantity']){if(!/^\d+$/.test(this.data[k])||!Number.isSafeInteger(Number(this.data[k]))){this.setData({error:'请填写非负整数数量。'});return}body[k]=Number(this.data[k])}}this.pending={key:requestKey(),body};return this.send()},
 async send(){if(!this.pending||this.data.busy)return;if(this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({error:'登录状态已改变，请重新打开门店中心。'});return;}this.setData({busy:true,error:''});try{await api.post(`${this.root()}/notes`,this.pending.body,{'Idempotency-Key':this.pending.key});this.pending=null;if(this.disposed||this.identity!==wx.getStorageSync('mall.memberToken'))return;this.setData({uncertain:false,note:'',receivedQuantity:'',salableQuantity:''});await this.load()}catch(e){if(e.statusCode&&e.statusCode<500&&![408,429].includes(e.statusCode)){this.pending=null;this.setData({error:e.message,uncertain:false})}else this.setData({error:'记录结果尚未确认，请重试同一请求。',uncertain:true})}finally{this.setData({busy:false})}},
})
