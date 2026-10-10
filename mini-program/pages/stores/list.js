const api = require('../../lib/api')
const stores = require('../../lib/stores')
Page({
 data:{state:'loading',error:'',locationError:'',rows:[],keyword:'',selectedId:''},
 onLoad(){this.location=null;this.setData({selectedId:(stores.selected()||{}).id||''});return this.load()},
 onUnload(){this.disposed=true},
 async load(){this.setData({state:'loading',error:''});try{const result=await api.get('/api/v1/app/stores',{q:this.data.keyword});if(this.disposed)return;this.setData({rows:stores.rank(result.items,this.location),state:result.items.length?'ready':'empty'})}catch(e){if(!this.disposed)this.setData({state:'error',error:e.message})}},
 input(e){this.setData({keyword:e.detail.value})},
 async relocate(){try{this.location=await stores.locate();if(!this.disposed)this.setData({locationError:''});return this.load()}catch(e){this.setData({locationError:e.message})}},
 choose(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(!row)return;stores.select(row);wx.navigateBack({delta:1,fail:()=>wx.redirectTo({url:'/pages/home/home'})})},
 phone(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(row&&row.contactPhone)wx.makePhoneCall({phoneNumber:row.contactPhone})},
 map(e){const row=this.data.rows.find(r=>r.id===e.currentTarget.dataset.id);if(row&&stores.coordinates(row))wx.openLocation({latitude:row.latitude,longitude:row.longitude,name:row.name,address:row.address})},
})
