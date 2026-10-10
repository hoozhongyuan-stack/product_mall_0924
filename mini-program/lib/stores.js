const KEY = 'mall.consumerStore.v1'
const MANAGED = 'mall.managedStore.v1'
const MODE_LABELS = { PICKUP: '到店自提', DELIVERY: '配送到家', EXPRESS: '快递发货' }
function selected() { const v = wx.getStorageSync ? wx.getStorageSync(KEY) : null; return v && typeof v.id === 'string' && v.id ? v : null }
function select(value) { if (!value || typeof value.id !== 'string' || !value.id) throw new Error('请选择有效门店。'); const before = selected(); wx.setStorageSync(KEY, { ...value }); if (!before || before.id !== value.id) wx.removeStorageSync('mall.checkoutSelection.v1') }
function clear() { wx.removeStorageSync(KEY) }
function managed() { return wx.getStorageSync(MANAGED) || null }
function manage(id) { wx.setStorageSync(MANAGED, id) }
function query() { const store = selected(); return store ? { storeId: store.id } : {} }
function coordinates(value) { return !!value && typeof value.latitude === 'number' && typeof value.longitude === 'number' && Number.isFinite(value.latitude) && Number.isFinite(value.longitude) && Math.abs(value.latitude) <= 90 && Math.abs(value.longitude) <= 180 }
function distance(a, b) {
 if (!coordinates(a) || !coordinates(b)) return null
 const rad = d => d * Math.PI / 180
 const h = Math.sin(rad(b.latitude-a.latitude)/2)**2 + Math.cos(rad(a.latitude))*Math.cos(rad(b.latitude))*Math.sin(rad(b.longitude-a.longitude)/2)**2
 return Math.round(6371000 * 2 * Math.asin(Math.min(1,Math.sqrt(h))))
}
function rank(rows, location) { return (Array.isArray(rows)?rows:[]).map(row => {const meters=distance(location,row); return {...row,distanceMeters:meters,distanceLabel:meters===null?'':meters<1000?`${meters} m`:`${(meters/1000).toFixed(1)} km`}}).sort((a,b)=>(a.distanceMeters??Infinity)-(b.distanceMeters??Infinity)) }
function locate() { return new Promise((resolve,reject)=>{ if (!wx.getLocation) {reject(new Error('定位暂不可用，请手动选择门店。'));return} wx.getLocation({type:'gcj02',success:resolve,fail:()=>reject(new Error('未获得定位权限，请手动选择门店。'))}) }) }
function stockPools(product) { const seen = new Set(); return (product.skus || []).filter(s => {const key=s.anchorSkuId||s.id; if(seen.has(key))return false;seen.add(key);return true}).map(s=>({...s,poolId:s.anchorSkuId||s.id,draft:String(s.availableBaseUnits)})) }
function stockPatch(product, drafts, onSale = product.onSale) { return {revision:product.revision,onSale,stock:stockPools(product).map(s=>{const v=String(drafts[s.poolId]??s.availableBaseUnits).trim();if(!/^\d+$/.test(v)||!Number.isSafeInteger(Number(v)))throw new Error('可售库存须填写非负整数。');return {skuId:s.id,availableBaseUnits:Number(v),expectedAvailableBaseUnits:s.availableBaseUnits}})} }
function modes(store) { return (store && Array.isArray(store.supportedModes)?store.supportedModes:[]).filter(m=>MODE_LABELS[m]).map(value=>({value,label:MODE_LABELS[value]})) }
module.exports = { selected, select, clear, managed, manage, query, coordinates, distance, rank, locate, stockPools, stockPatch, modes, MODE_LABELS }
