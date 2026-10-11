const {money,mediaUrl}=require('./catalog')
const {dateLabel}=require('./orders')
const SECTIONS=[{value:'products',label:'商品',permission:'products'},{value:'orders',label:'订单',permission:'orders'},{value:'account',label:'佣金',permission:'finance'}]
function sections(restaurant){return SECTIONS.filter(s=>!Array.isArray(restaurant&&restaurant.permissions)||restaurant.permissions.includes(s.permission))}
function amount(fen){return Number.isSafeInteger(fen)&&fen>=0?money(fen):'待核对'}
function product(row,baseUrl){
 const skus=(row.skus||[]).map(s=>({...s,label:s.specLabel||'默认规格',price:amount(s.unitPriceFen),stock:Number.isSafeInteger(s.availableQuantity)&&s.availableQuantity>=0?`${s.availableQuantity} ${s.saleUnit||''}`:'库存待核对'}))
 const prices=skus.map(s=>s.unitPriceFen).filter(v=>Number.isSafeInteger(v)&&v>=0)
 const platformOnSale=row.platformStatus==='ON_SALE',warehouseOnSale=row.warehouseOnSale===true
 const saleLabel=!platformOnSale?'平台已下架':!warehouseOnSale?'履约仓已下架':row.onSale?'销售中':'本店已下架'
 const shortage=skus.some(s=>s.availableQuantity===0)
 return {...row,imageUrl:mediaUrl(baseUrl,row.mainImageUrl),skus,priceLabel:prices.length?`${money(Math.min(...prices))}${new Set(prices).size>1?'起':''}`:'价格待确认',stockLabel:skus.length?skus.slice(0,2).map(s=>`${s.label} ${s.stock}`).join(' · '):'库存待配置',specCount:skus.length,canPutOnSale:platformOnSale,saleLabel,saleTone:row.onSale&&platformOnSale&&warehouseOnSale?'active':'inactive',shortage}
}
function order(row){
 const delivery=row.delivery||{}
 const deliveryLabels={WAITING_PREPARATION:'待备货',WAITING_DELIVERY:'待配送',READY:'待配送',IN_TRANSIT:'配送中',COMPLETED:'已完成'}
 const status=row.status==='CLOSED'?'已关闭':row.status==='PENDING_PAYMENT'?'待付款':row.status==='PAID'?(deliveryLabels[delivery.status]||'已付款'):'状态待核对'
 return {...row,id:row.id||row.orderId,amount:amount(row.payableFen),date:dateLabel(row.createdAt),statusLabel:status,commission:row.commission?{...row.commission,amount:amount(row.commission.commissionFen),statusLabel:INCOME_LABELS[row.commission.status]||'状态待核对'}:null,lines:(row.lines||[]).map(line=>({...line,name:line.productName||line.name||'商品',amount:amount(line.payableFen)}))}
}
const INCOME_LABELS={ESTIMATED:'预计佣金',PENDING:'已入账，待过售后期',AVAILABLE:'可提现',HELD:'冻结待处理'}
const WITHDRAWAL_LABELS={PENDING:'待审核',APPROVED:'审核通过，待打款',REJECTED:'已驳回'}
function account(row){
 return {...row,available:amount(row.availableFen),pending:amount(row.pendingFen),held:amount(row.heldFen),frozen:amount(row.frozenFen),total:amount(row.totalCommissionFen),canApply:Number.isSafeInteger(row.availableFen)&&row.availableFen>0,
 incomes:(row.incomes||[]).map(i=>({...i,amount:amount(i.commissionFen),statusLabel:INCOME_LABELS[i.status]||'状态待核对',availableDate:dateLabel(i.availableAt),bookedDate:dateLabel(i.bookedAt)})),
 withdrawals:(row.withdrawals||[]).map(w=>({...w,amount:amount(w.amountFen),statusLabel:WITHDRAWAL_LABELS[w.status]||'状态待核对',date:dateLabel(w.createdAt)}))}
}
module.exports={product,order,account,sections}
