const api = require('../../lib/api')
const stores = require('../../lib/stores')
const delivery = require('../../lib/delivery-address')
Page({
 data:{state:'loading',error:'',locationError:'',rows:[],keyword:'',selectedId:'',addressMode:false,busy:false},
 async onLoad(options={}){this.disposed=false;this.hidden=false;this.location=null;this.addressId=options.addressId||'';this.identity=wx.getStorageSync('mall.memberToken');this.setData({selectedId:(stores.selected()||{}).id||'',addressMode:!!this.addressId});if(this.addressId){try{const rows=await api.get('/api/v1/app/addresses');if(this.disposed||this.identity!==wx.getStorageSync('mall.memberToken'))return;const a=rows.find(a=>a.id===this.addressId);if(!delivery.located(a))throw new Error('请先补全收货位置，再查找配送前置仓。');this.location={latitude:delivery.coordinate(a.latitude,90),longitude:delivery.coordinate(a.longitude,180)}}catch(e){this.setData({state:'error',error:e.message});return}}return this.load()},
 onShow(){if(this.hidden){this.hidden=false;return this.load()}},
 onHide(){this.hidden=true;this.generation=(this.generation||0)+1;this.setData({rows:[],busy:false})},
 onUnload(){this.disposed=true;this.onHide()},
 async load(){if(this.addressId&&this.identity!==wx.getStorageSync('mall.memberToken')){this.setData({state:'error',rows:[],error:'登录状态已改变，请返回地址列表重新选择。'});return}if(this.addressId&&!this.location){this.setData({state:'error',error:'收货位置未加载，请返回地址列表重试。'});return}const gen=this.generation=(this.generation||0)+1;this.setData({state:'loading',error:''});try{const result=await api.get('/api/v1/app/stores',{q:this.data.keyword});if(this.disposed||this.hidden||gen!==this.generation||(this.addressId&&this.identity!==wx.getStorageSync('mall.memberToken')))return;this.setData({rows:stores.rank(result.items,this.location),state:result.items.length?'ready':'empty'})}catch(e){if(!this.disposed)this.setData({state:'error',error:e.message})}},
 input(e){this.setData({keyword:e.detail.value})},
 async relocate(){try{this.location=await stores.locate();if(!this.disposed)this.setData({locationError:''});return this.load()}catch(e){this.setData({locationError:e.message})}},
 async choose(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(!row||this.data.busy)return;if(!this.addressId){stores.select(row);wx.navigateBack({delta:1,fail:()=>wx.redirectTo({url:'/pages/home/home'})});return}
  const identity=this.identity,gen=this.generation;const current=()=>!this.disposed&&!this.hidden&&gen===this.generation&&identity===wx.getStorageSync('mall.memberToken')
  this.setData({busy:true,error:''});try{const result=await api.get('/api/v1/app/address-locations/eligibility',{storeId:row.id,...this.location});if(!current())return;if(result.code!=='ELIGIBLE'){this.setData({error:result.message||'该前置仓暂不能配送此地址。'});return}
   wx.showModal({title:'切换配送前置仓',content:'切换后返回首页，按该仓商品、库存与配送费用重新选购。原前置仓购物车会保留。',confirmText:'切换并选购',success:r=>{if(r.confirm&&current()){wx.setStorageSync('mall.selectedAddressId',this.addressId);stores.select(row);wx.redirectTo({url:'/pages/home/home'})}}})
  }catch(e){if(current())this.setData({error:e.message})}finally{if(current())this.setData({busy:false})}
 },
 phone(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(row&&row.contactPhone)wx.makePhoneCall({phoneNumber:row.contactPhone})},
 map(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(row&&stores.coordinates(row))wx.openLocation({latitude:row.latitude,longitude:row.longitude,name:row.name,address:row.address})},
})
