const { mediaUrl } = require('./catalog')
function name(sku, index, total) {
 if (typeof sku.specLabel === 'string' && sku.specLabel.trim()) return sku.specLabel.trim()
 return total === 1 ? '默认规格' : `规格名称待补全（${index + 1}）`
}
function pools(product, drafts = {}) {
 const skus = product.skus || []
 const keys = [...new Set(skus.map(s => s.anchorSkuId || s.id))]
 return keys.map(key => {
  const members = skus.filter(s => (s.anchorSkuId || s.id) === key)
  const anchor = members.find(s => s.id === key) || members[0]
  const draft = String(drafts[key] ?? anchor.availableBaseUnits)
  const valid = /^\d+$/.test(draft) && Number.isSafeInteger(Number(draft))
  const variants = members.map(s => ({id:s.id,label:name(s,skus.indexOf(s),skus.length),availableLabel:valid && Number.isSafeInteger(s.ratio) && s.ratio > 0 ? `${Math.floor(Number(draft)/s.ratio)} ${s.saleUnit || s.baseUnit || ''}` : '数量待核对'}))
  return {...anchor,poolId:key,draft,label:variants.map(s=>s.label).join(' / '),shared:members.length>1,variants}
 })
}
function present(product, baseUrl) {
 const grouped = pools(product)
 const skus = product.skus || []
 const shortage = skus.filter(s => (!s.platformStatus || s.platformStatus === 'ON_SALE') && Number.isSafeInteger(s.ratio) && s.ratio > 0 && s.availableBaseUnits < s.ratio).length
 const platformOnSale = product.platformStatus === 'ON_SALE' || product.platformStatus === 'ACTIVE'
 const single = skus.length === 1 && grouped[0]
 return {...product,pools:grouped,specCount:skus.length,shared:grouped.some(s=>s.shared),platformOnSale,canPutOnSale:platformOnSale,imageUrl:mediaUrl(baseUrl,product.mainImageUrl),effectiveOnSale:product.onSale && platformOnSale,stockSummary:!skus.length?'库存待配置':single?`可售 ${single.availableBaseUnits} ${single.baseUnit || ''}`:shortage?`${shortage} 个规格缺货`:'规格均有库存',shortage:shortage>0}
}
function edit(product, drafts) {
 const grouped=pools(product,drafts)
 const scrollHeight=grouped.reduce((height,pool)=>height+170+(pool.shared?pool.variants.length*70+32:0),150)
 return {...product,pools:grouped,scrollHeight}
}
function batch(product, value) {
 const text = String(value).trim()
 if (!/^\d+$/.test(text) || (!Number.isSafeInteger(Number(text)) || Number(text) > 1000000000)) throw new Error('可售库存须填写非负整数，且不超过10亿。')
 return Object.fromEntries(pools(product).map(pool=>[pool.poolId,text]))
}
const FILTERS=[{value:'',label:'全部'},{value:'ON_SALE',label:'销售中'},{value:'OFF_SALE',label:'已下架'},{value:'SHORTAGE',label:'缺货'}]
module.exports={pools,present,edit,batch,FILTERS}
