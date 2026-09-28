const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const object=v=>!!v&&typeof v==='object'&&!Array.isArray(v)
export function offerError(skuId,price){if(!uuid.test(skuId))return '请从 SKU 查询结果选择兑换商品。';if(typeof price!=='string'||!/^\d+$/.test(price)||Number(price)<1||Number(price)>1000000)return '兑换积分须为 1 至 1000000 的整数。';return ''}
export function offerBody(skuId,price,revision){return revision===undefined?{skuId,pointsPrice:Number(price)}:{expectedRevision:revision,pointsPrice:Number(price)}}
export function resultMatches(p,r){
 if(!object(p)||!object(p.body)||!object(r))return false
 const match=/^\/exchange-offers(?:\/([0-9a-f-]{36})(\/availability)?)?$/.exec(p.path);if(!match)return false
 const [,id,availability]=match,b=p.body
 if(!uuid.test(r.id)||!uuid.test(r.skuId)||!uuid.test(r.productId)||typeof r.productName!=='string'||typeof r.skuCode!=='string'||typeof r.saleUnit!=='string'||!['SHIP','REDEEM'].includes(r.fulfillmentKind)||!['DRAFT','ON_SALE','OFF_SALE'].includes(r.status)||!Number.isSafeInteger(r.pointsPrice)||r.pointsPrice<1||r.pointsPrice>1000000||!Array.isArray(r.specs)||r.specs.some(s=>!object(s)||typeof s.name!=='string'||typeof s.value!=='string')||!Number.isFinite(Date.parse(r.createdAt)))return false
 if(id&&r.id!==id||r.revision!==(id?b.expectedRevision+1:1))return false
 if(availability)return p.action==='exchange.publish'&&r.status===b.status
 return p.action===''&&r.pointsPrice===b.pointsPrice&&(id||r.status==='DRAFT'&&r.skuId===b.skuId)?true:false
}
export function recoveryDecision(r,p){return object(r)&&r.status==='NOT_FOUND'?'RETRY':object(r)&&r.status==='COMPLETED'&&resultMatches(p,r.result)?'COMPLETED':'UNKNOWN'}
export function definitiveRejection(status){return [400,403,404,405,409,410,413,415,422].includes(status)}
export function pendingExchange(storage,actor,target,value=undefined){const key=`mall:exchange:${actor}:${target}`;if(value===null){storage.removeItem(key);return null}if(value!==undefined){storage.setItem(key,JSON.stringify(value));return value}const raw=storage.getItem(key);if(!raw)return null;try{const p=JSON.parse(raw);if(object(p)&&uuid.test(p.key)&&typeof p.path==='string'&&/^\/exchange-offers(?:\/[0-9a-f-]{36}(?:\/availability)?)?$/.test(p.path)&&['POST','PUT'].includes(p.method)&&object(p.body)&&!('password' in p)&&!('password' in p.body)&&['','exchange.publish'].includes(p.action))return p}catch{}storage.removeItem(key);return null}
