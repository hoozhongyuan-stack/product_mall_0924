const api = require('../../lib/api')
const delivery = require('../../lib/delivery-address')
const stores = require('../../lib/stores')

Page({
  data: { state: 'loading', error: '', addresses: [], selecting: false, selectedId: '', deliveryMode:'',storeId:'',storeName:'', selectionError:'' },
  onLoad(options) { this.hidden=false;this.disposed=false;this.storeId=options.storeId||'';this.deliveryMode=options.deliveryMode==='DELIVERY'&&this.storeId?'DELIVERY':'';this.setData({selecting:options.select==='1',selectedId:wx.getStorageSync('mall.selectedAddressId')||'',deliveryMode:this.deliveryMode,storeId:this.storeId,storeName:(stores.selected()||{}).name||'当前前置仓'}) },
  onShow() { this.hidden=false;return this.refresh() },
  onHide() { this.hidden=true;this.generation=(this.generation||0)+1;this.setData({addresses:[],selectionError:''}) },
  onUnload() { this.disposed=true;this.onHide() },
  current(gen,identity) { return !this.hidden&&!this.disposed&&gen===this.generation&&identity===wx.getStorageSync('mall.memberToken') },
  async refresh() {
    const gen=this.generation=(this.generation||0)+1,identity=wx.getStorageSync('mall.memberToken')
    this.setData({state:'loading',error:'',addresses:[],selectionError:''})
    try {
      const rows=await api.get('/api/v1/app/addresses',this.deliveryMode==='DELIVERY'?{storeId:this.storeId}:{})
      if(!this.current(gen,identity))return
      this.loadedIdentity=identity;this.setData({addresses:rows.map(delivery.present),state:rows.length?'ready':'empty'})
    } catch(error) { if(!this.hidden&&!this.disposed&&gen===this.generation&&(identity===wx.getStorageSync('mall.memberToken')||(!wx.getStorageSync('mall.memberToken')&&error.statusCode===401)))this.setData({addresses:[],state:error.statusCode===401?'auth':'error',error:error.message}) }
  },
  select(event) {
    const address=this.data.addresses.find(a=>a.id===event.currentTarget.dataset.id)
    if(!this.data.selecting||!address)return
    if(this.hidden||this.disposed||this.loadedIdentity!==wx.getStorageSync('mall.memberToken')){this.setData({addresses:[],state:'auth',error:'登录状态已改变，请重新进入地址列表。'});return}
    if(this.deliveryMode==='DELIVERY'&&((stores.selected()||{}).id!==this.storeId||!delivery.allowed(address,'DELIVERY'))){this.setData({selectionError:address.eligibility.message||'请重新核对当前前置仓与收货地址。'});return}
    wx.setStorageSync('mall.selectedAddressId',address.id);wx.navigateBack({delta:1})
  },
  login() { wx.navigateTo({ url: '/pages/login/login?returnTo=addresses' }) },
  add() { wx.navigateTo({url:'/pages/addresses/edit'+(this.deliveryMode==='DELIVERY'?`?storeId=${encodeURIComponent(this.storeId)}&deliveryMode=DELIVERY&select=${this.data.selecting?'1':'0'}`:'')}) },
  edit(event) { wx.navigateTo({ url: `/pages/addresses/edit?id=${encodeURIComponent(event.currentTarget.dataset.id)}${delivery.context(this.storeId,this.deliveryMode)}${this.deliveryMode==='DELIVERY'?'&select='+(this.data.selecting?'1':'0'):''}` }) },
  otherStores(event) { const a=this.data.addresses.find(a=>a.id===event.currentTarget.dataset.id);if(a&&delivery.located(a))wx.navigateTo({url:`/pages/stores/list?addressId=${encodeURIComponent(a.id)}`}) },
  remove(event) {
    const address = this.data.addresses.find((item) => item.id === event.currentTarget.dataset.id)
    if (!address) return
    const gen=this.generation,identity=wx.getStorageSync('mall.memberToken')
    wx.showModal({ title: '删除收货地址', content: '删除后不能恢复。', success: async (result) => {
      if (!result.confirm || !this.current(gen,identity)) return
      try { await api.remove(`/api/v1/app/addresses/${address.id}`, { expectedRevision: address.revision }); if(this.current(gen,identity))this.refresh() }
      catch (error) { wx.showToast({ title: error.message, icon: 'none' }); if(this.current(gen,identity))this.refresh() }
    } })
  },
})
