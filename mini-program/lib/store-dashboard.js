const TODOS=[['waitingPreparation','待备货','WAITING_PREPARATION'],['waitingPickup','待自提','WAITING_PICKUP'],['waitingDelivery','待配送','WAITING_DELIVERY'],['waitingShipment','待发货','WAITING_SHIPMENT']]
const SHORTCUTS=[
 {key:'products',label:'商品与库存',section:'products',permission:'products',icon:'parcel'},
 {key:'orders',label:'全部订单',section:'orders',permission:'orders',icon:'shipment'},
 {key:'verify',label:'自提核验',section:'orders',fulfillment:'WAITING_PICKUP',permission:'orders',icon:'voucher'},
 {key:'aftersales',label:'售后管理',section:'aftersales',permission:'orders',icon:'exchange'},
 {key:'account',label:'前置仓账户',section:'account',accountPanel:'summary',permission:'accounts',icon:'payment'},
 {key:'income',label:'收入明细',section:'account',accountPanel:'income',permission:'accounts',icon:'points'},
 {key:'withdraw',label:'申请提现',section:'account',accountPanel:'withdraw',permission:'accounts',icon:'payment'},
 {key:'history',label:'提现记录',section:'account',accountPanel:'history',permission:'accounts',icon:'voucher'},
]
function count(value){return Number.isSafeInteger(value)&&value>=0?value:null}
function present(overview,permissions){const stats=overview.stats||{};return {
 todos:TODOS.map(([key,label,fulfillment])=>({key,label,fulfillment,count:count(stats[key])})),
 totalOrders:count(stats.totalOrders),activeAfterSales:count(stats.activeAfterSales),
 shortcuts:SHORTCUTS.filter(x=>permissions.includes(x.permission)).map(x=>({...x,icon:`/assets/ui/${x.icon}.svg`})),
}}
function query(section,page,data){
 if(section==='products')return {page,pageSize:20,search:data.keyword||''}
 if(section==='account')return {}
 return {page,...(section==='orders'&&data.orderFilter?{fulfillment:data.orderFilter}:{}),...(section==='aftersales'&&data.activeAftersales?{status:'ACTIVE'}:{})}
}
const ORDER_FILTERS=[{value:'',label:'全部'},...TODOS.map(([,label,value])=>({value,label}))]
function orderStatus(order){
 if(order.status!=='PAID')return ({PENDING_PAYMENT:'待付款',CLOSED:'已关闭'})[order.status]||order.status
 const fact=order.fulfillmentStatus
 const state=['AFTER_SALE','REFUNDED'].includes(fact)?fact:(order.storeDelivery&&order.storeDelivery.status)||fact
 return ({WAITING_PREPARATION:'待备货',WAITING_PICKUP:'待自提',WAITING_DELIVERY:'待配送',WAITING_SHIPMENT:'待发货',IN_TRANSIT:order.deliveryMode==='DELIVERY'?'配送中':'已发货',DELIVERING:'配送中',IN_PROGRESS:'履约中',COMPLETED:'已完成',REFUNDED:'已退款',AFTER_SALE:'售后处理中',WAITING_REDEMPTION:'待核销'})[state]||'已付款'
}
function emptyOrders(filter){const item=ORDER_FILTERS.find(x=>x.value===filter);return item&&item.value?`暂无${item.label}订单。`:'本店暂无订单。'}
module.exports={present,query,orderStatus,ORDER_FILTERS,emptyOrders}
