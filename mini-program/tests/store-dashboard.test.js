const test=require('node:test'),assert=require('node:assert/strict')
const dashboard=require('../lib/store-dashboard')
test('overview preserves unknown counts and zero, and emits permission-specific shortcuts',()=>{
 const summary=dashboard.present({stats:{waitingPreparation:2,waitingPickup:0,waitingDelivery:1,waitingShipment:3,totalOrders:8,activeAfterSales:1}},['orders','accounts'])
 assert.equal(summary.todos[1].count,0);assert.equal(summary.totalOrders,8)
 assert.ok(summary.shortcuts.some(x=>x.key==='verify'&&x.fulfillment==='WAITING_PICKUP'))
 assert.ok(!summary.shortcuts.some(x=>x.section==='products'))
 assert.equal(dashboard.present({stats:null},['products']).totalOrders,null)
 assert.equal(dashboard.present({stats:{}},['orders']).todos[0].count,null)
 assert.equal(dashboard.present({stats:null},[]).shortcuts.length,0)
})
const storage=new Map();global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k)}
const api=require('../lib/api')
function mount(){let config;global.Page=c=>config=c;delete require.cache[require.resolve('../pages/store-center/index')];require('../pages/store-center/index');return {...config,data:structuredClone(config.data),setData(p){this.data={...this.data,...p}}}}
function ready(permissions){storage.clear();storage.set('mall.memberToken','member');const p=mount();p.identity='member';p.choose({id:'s',name:'仓',permissions});return p}
test('dashboard loads real overview and requests account only with accounts permission',async()=>{
 const old=api.get,paths=[];api.get=async path=>{paths.push(path);return path.endsWith('/account')?{balance:{availableFen:9200}}:{stats:{waitingPickup:4,totalOrders:9}}}
 const p=ready(['orders']);await p.loadSection();assert.equal(p.data.overview.todos[1].count,4);assert.ok(!paths.some(x=>x.endsWith('/account')))
 const q=ready(['accounts']);await q.loadSection();assert.equal(q.data.account.available,'¥92.00');api.get=old
})
test('todo and account shortcuts use real filters and isolated account subviews',async()=>{
 const old=api.get,queries=[];api.get=async(path,query)=>{queries.push({path,query});return path.endsWith('/account')?{balance:{availableFen:0}}:{items:[],total:0}}
 const p=ready(['orders','accounts']);await p.section({currentTarget:{dataset:{section:'orders',fulfillment:'WAITING_PICKUP'}}});assert.equal(queries[0].query.fulfillment,'WAITING_PICKUP')
 await p.section({currentTarget:{dataset:{section:'aftersales',active:true}}});assert.equal(queries[1].query.status,'ACTIVE')
 await p.section({currentTarget:{dataset:{section:'account',accountPanel:'income'}}});assert.equal(p.data.accountPanel,'income');assert.equal(p.data.section,'account')
 await p.section({currentTarget:{dataset:{section:'orders'}}});assert.equal(queries[3].query.fulfillment,undefined);api.get=old
})
test('account load failure keeps order dashboard usable without fabricated balances',async()=>{
 const old=api.get;api.get=async path=>{if(path.endsWith('/account'))throw new Error('暂不可读取账户');return {stats:{waitingPickup:2}}}
 const p=ready(['orders','accounts']);await p.loadSection();assert.equal(p.data.state,'ready');assert.equal(p.data.account,null);assert.match(p.data.accountError,/暂不可读取/);api.get=old
})
test('queries use only server-supported fields',()=>{
 assert.deepEqual(dashboard.query('orders',2,{orderFilter:'WAITING_PICKUP',keyword:'ignored'}),{page:2,fulfillment:'WAITING_PICKUP'})
 assert.deepEqual(dashboard.query('aftersales',1,{activeAftersales:true}),{page:1,status:'ACTIVE'})
 assert.deepEqual(dashboard.query('account',1,{}),{})
 assert.deepEqual(dashboard.query('products',1,{keyword:'啤酒'}),{page:1,pageSize:20,search:'啤酒'})
})
test('overview errors enter recoverable state and cannot leave loading indefinitely',async()=>{
 const old=api.get;api.get=async()=>{throw new Error('统计加载失败')};const p=ready(['orders']);await p.loadSection();assert.equal(p.data.state,'error');assert.match(p.data.error,/统计加载失败/);api.get=old
})
test('order status prioritizes real fulfillment facts and keeps active aftersale visible',()=>{
 assert.equal(dashboard.orderStatus({status:'PAID',fulfillmentStatus:'WAITING_PICKUP'}),'待自提')
 assert.equal(dashboard.orderStatus({status:'PAID',deliveryMode:'DELIVERY',storeDelivery:{status:'IN_TRANSIT'}}),'配送中')
 assert.equal(dashboard.orderStatus({status:'PAID',storeDelivery:{status:'COMPLETED'},fulfillmentStatus:'AFTER_SALE'}),'售后处理中')
 assert.equal(dashboard.orderStatus({status:'CLOSED',fulfillmentStatus:'WAITING_PICKUP'}),'已关闭')
})
test('overview live permissions replace cached shortcuts and discard no-longer-authorized balances',async()=>{
 const old=api.get;api.get=async path=>path.endsWith('/account')?{balance:{availableFen:500}}:{permissions:['products'],stats:null}
 const p=ready(['orders','accounts']);await p.loadSection();assert.equal(p.data.canOrders,false);assert.equal(p.data.canAccounts,false);assert.equal(p.data.account,null);assert.deepEqual(p.data.overview.shortcuts.map(x=>x.key),['products']);api.get=old
})
test('new warehouse without previous section permission returns to workbench and stale overview is ignored',async()=>{
 const p=ready(['orders']);p.data.section='orders';p.choose({id:'new',permissions:['products']});assert.equal(p.data.section,'home');assert.equal(p.data.canOrders,false)
 const old=api.get;let finish;api.get=()=>new Promise(resolve=>finish=resolve);const q=ready(['orders']);const request=q.loadSection();storage.set('mall.memberToken','other');finish({permissions:['orders'],stats:{totalOrders:999}});await request;assert.equal(q.data.overview,null);api.get=old
})
test('late stock save after hide or member change never reloads products or shows success',async()=>{
 const oldGet=api.get,oldPatch=api.patch;const savedToast=wx.showToast;let reads=0,toasts=0;api.get=async()=>{reads++;return {items:[]}};wx.showToast=()=>toasts++
 for(const change of ['hide','member']){
  const p=ready(['products']);p.generation=1;p.data.section='products';p.data.products=[{id:'p',revision:1,onSale:true,skus:[{id:'s',availableBaseUnits:10}]}];let finish;api.patch=()=>new Promise(resolve=>finish=resolve)
  const request=p.saveProduct({currentTarget:{dataset:{id:'p'}}});if(change==='hide')p.onHide();else storage.set('mall.memberToken','other');finish({});await request
  assert.equal(reads,0);assert.equal(toasts,0);if(change==='hide')assert.deepEqual(p.data.products,[])
 }
 api.get=oldGet;api.patch=oldPatch;wx.showToast=savedToast
})
test('filtered lists explain their actual empty state',()=>{
 assert.equal(dashboard.emptyOrders('WAITING_PICKUP'),'暂无待自提订单。')
 assert.equal(dashboard.emptyOrders(''),'本店暂无订单。')
})
test('expired member token removed by API enters auth instead of remaining loading',async()=>{
 const old=api.get;api.get=async()=>{storage.delete('mall.memberToken');const e=new Error('请登录');e.statusCode=401;throw e}
 const p=ready(['orders']);await p.loadSection();assert.equal(p.data.state,'auth');assert.equal(p.data.store,null);assert.equal(p.data.canOrders,false)
 const q=ready(['orders']);await q.loadStores();assert.equal(q.data.state,'auth');assert.deepEqual(q.data.orders,[]);api.get=old
})
test('parallel overview account expiry clears private view but a new member response is ignored',async()=>{
 const old=api.get;api.get=async path=>{if(path.endsWith('/account')){storage.delete('mall.memberToken');const e=new Error('已过期');e.statusCode=401;throw e}return {stats:{totalOrders:10}}}
 const p=ready(['orders','accounts']);await p.loadSection();assert.equal(p.data.state,'auth');assert.equal(p.data.account,null);assert.equal(p.data.overview,null)
 const q=ready(['orders']);api.get=async()=>{storage.set('mall.memberToken','new');const e=new Error('旧请求');e.statusCode=401;throw e};await q.loadSection();assert.notEqual(q.data.state,'auth');assert.equal(storage.get('mall.memberToken'),'new');api.get=old
})
test('ordinary product expiry clears view and hidden 401 cannot update a departed page',async()=>{
 const old=api.get;api.get=async()=>{storage.delete('mall.memberToken');const e=new Error('过期');e.statusCode=401;throw e}
 const p=ready(['products']);p.data.section='products';await p.loadSection();assert.equal(p.data.state,'auth');assert.deepEqual(p.data.products,[])
 const q=ready(['products']);q.generation=3;q.onHide();storage.delete('mall.memberToken');assert.equal(q.authExpired(q.generation,{statusCode:401}),false);assert.notEqual(q.data.state,'auth');api.get=old
})
test('returning after deferred stock save releases stale busy without restoring old operation',async()=>{
 const oldGet=api.get,oldPatch=api.patch,oldToast=wx.showToast;let finish;let toasts=0;wx.showToast=()=>toasts++
 const p=ready(['products']);p.generation=1;p.data.section='products';p.data.products=[{id:'p',revision:1,onSale:true,skus:[{id:'s',availableBaseUnits:10}]}]
 api.patch=()=>new Promise(resolve=>finish=resolve);api.get=async path=>path.endsWith('/products')?{items:[{id:'new',skus:[]}],total:1}:{items:[{id:'s',permissions:['products']}]}
 const request=p.saveProduct({currentTarget:{dataset:{id:'p'}}});assert.equal(p.data.busy,true);p.onHide();await p.onShow();assert.equal(p.data.busy,false);finish({});await request;assert.equal(toasts,0);assert.equal(p.data.products[0].id,'new');api.get=oldGet;api.patch=oldPatch;wx.showToast=oldToast
})
test('stock save reload failure does not announce successful recheck',async()=>{
 const oldGet=api.get,oldPatch=api.patch,oldToast=wx.showToast;let toasts=0;wx.showToast=()=>toasts++;api.patch=async()=>({});api.get=async()=>{throw new Error('重载失败')}
 const p=ready(['products']);p.generation=1;p.data.section='products';p.data.products=[{id:'p',revision:1,onSale:true,skus:[{id:'s',availableBaseUnits:10}]}];await p.saveProduct({currentTarget:{dataset:{id:'p'}}});assert.equal(p.data.state,'error');assert.equal(toasts,0);assert.equal(p.data.busy,false);api.get=oldGet;api.patch=oldPatch;wx.showToast=oldToast
})
test('financial discovery remains visible without account permission but exposes no balance',()=>{
 const shown=dashboard.present({stats:null},['products']);assert.equal(shown.financeShortcuts.length,4);assert.deepEqual(shown.businessShortcuts.map(row=>row.key),['products']);assert.equal(shown.financeShortcuts[1].accountPanel,'income');assert.equal(Object.hasOwn(shown,'balance'),false)
})
