export interface ProfitBatchItem {skuId:string;expectedRevision:number}
export interface ProfitBatchBody {items:ProfitBatchItem[];purchaseCostFen:number;platformShareBps:number}
export function canonicalBatchItems(items:ProfitBatchItem[]):ProfitBatchItem[]{return items.map(item=>({...item,skuId:item.skuId.toLowerCase()})).sort((a,b)=>a.skuId.localeCompare(b.skuId))}
export async function profitBatchTarget(body:ProfitBatchBody):Promise<string>{
  const canonical=JSON.stringify([body.purchaseCostFen,body.platformShareBps,canonicalBatchItems(body.items).map(item=>[item.skuId,item.expectedRevision])])
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical))
  return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')
}
