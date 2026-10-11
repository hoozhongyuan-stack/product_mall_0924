const {test}=require('node:test')
const assert=require('node:assert/strict')
const view=require('../lib/dining-center')
test('management products show real images, member prices and each SKU stock without adding mixed units',()=>{
 const product={id:'p',name:'啤酒',mainImageUrl:'/api/v1/app/assets/img/file',platformStatus:'ON_SALE',warehouseOnSale:true,onSale:true,skus:[{id:'s1',specLabel:'单瓶',unitPriceFen:1000,availableQuantity:20,saleUnit:'瓶'},{id:'s2',specLabel:'整箱',unitPriceFen:10000,availableQuantity:2,saleUnit:'箱'}]}
 const shown=view.product(product,'https://uat.test');assert.equal(shown.imageUrl,'https://uat.test/api/v1/app/assets/img/file');assert.equal(shown.priceLabel,'¥10起');assert.equal(shown.stockLabel,'单瓶 20 瓶 · 整箱 2 箱');assert.equal(shown.saleLabel,'销售中');assert.equal(product.skus[0].availableQuantity,20)
 assert.equal(view.product({...product,warehouseOnSale:false},'').saleLabel,'履约仓已下架');assert.equal(view.product({...product,platformStatus:'OFF_SALE'},'').canPutOnSale,false)
 assert.equal(view.product({id:'x',skus:[]},'').priceLabel,'价格待确认');assert.equal(view.product({...product,mainImageUrl:'https://untrusted.test/p'},'').imageUrl,'')
})
test('order presentation uses delivery facts and server target and real amount',()=>{
 const shown=view.order({id:'o',orderNo:'DA001',status:'PAID',payableFen:10000,createdAt:'2026-10-11T03:00:00Z',tableNumber:'A01',delivery:{status:'IN_TRANSIT'},fulfillmentStore:{name:'前置仓'},lines:[{id:'l',productName:'啤酒',quantity:2,saleUnit:'瓶',payableFen:10000}]});assert.equal(shown.statusLabel,'配送中');assert.equal(shown.amount,'¥100');assert.equal(shown.lines[0].amount,'¥100');assert.equal(shown.lines[0].name,'啤酒');assert.equal(shown.tableNumber,'A01');assert.equal(view.order({status:'CLOSED'}).statusLabel,'已关闭');assert.equal(view.order({status:'PAID',delivery:{status:'COMPLETED'}}).statusLabel,'已完成')
})
test('finance presentation preserves unknown/zero and distinguishes approval from payment',()=>{
 const shown=view.account({availableFen:0,pendingFen:200,heldFen:100,frozenFen:300,payoutEnabled:false,incomes:[{orderId:'o',commissionFen:800,status:'ESTIMATED',tableNumber:'A1'}],withdrawals:[{id:'w',amountFen:300,status:'APPROVED',createdAt:'2026-10-11T03:00:00Z'}]});assert.equal(shown.available,'¥0');assert.equal(shown.incomes[0].statusLabel,'预计佣金');assert.equal(shown.withdrawals[0].statusLabel,'审核通过，待打款');assert.equal(shown.canApply,false);assert.equal(view.account({}).available,'待核对');assert.equal(view.account({availableFen:200,payoutEnabled:false}).canApply,true)
 assert.deepEqual(view.sections({permissions:['orders','finance']}).map(s=>s.value),['orders','account'])
})
const storage=new Map()
global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k)}
const api=require('../lib/api')
function mount(){let definition;global.Page=d=>definition=d;delete require.cache[require.resolve('../pages/dining/center')];require('../pages/dining/center');return {...definition,data:structuredClone(definition.data),setData(p){this.data={...this.data,...p}}}}
function ready(permissions=['products','orders','finance']){storage.clear();storage.set('mall.memberToken','member');const p=mount();p.active=true;p.identity='member';p.generation=1;p.setData({restaurant:{id:'r',permissions}});return p}
function deferred(){let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)}}
test('center chooses an authorized tab and blocks switching to unauthorized products',async()=>{
 const p=ready(['orders']),old=api.get;const calls=[];api.get=async path=>{calls.push(path);return path.endsWith('/restaurants')?{items:[{id:'r',permissions:['orders']}]}:{items:[],total:0}}
 try{await p.load();assert.equal(p.data.section,'orders');assert.deepEqual(p.data.sections.map(s=>s.value),['orders']);await p.section({currentTarget:{dataset:{section:'products'}}});assert.equal(calls.length,2);assert.equal(p.data.section,'orders')}finally{api.get=old}
})
test('center only applies latest requested order detail and close invalidates late responses',async()=>{
 const p=ready(),old=api.get,first=deferred(),second=deferred();api.get=path=>path.endsWith('/first')?first.promise:second.promise
 try{const a=p.openOrder({currentTarget:{dataset:{id:'first'}}}),b=p.openOrder({currentTarget:{dataset:{id:'second'}}});second.resolve({id:'second',status:'PAID',payableFen:200});await b;first.resolve({id:'first'});await a;assert.equal(p.data.detail.id,'second');assert.equal(p.data.detailLoading,false);const third=deferred();api.get=()=>third.promise;const c=p.openOrder({currentTarget:{dataset:{id:'third'}}});p.closeDetail();third.resolve({id:'third'});await c;assert.equal(p.data.detail,null);assert.equal(p.data.detailId,'')}finally{api.get=old}
})
test('center preserves pending withdrawal while blocking restaurant and finance tab switching',()=>{
 const p=ready();p.pending={identity:'member',restaurantId:'r',body:{amountFen:100,requestKey:'key'}};p.setData({pending:true,restaurants:[{id:'other',permissions:['finance']}],section:'account',accountPanel:'overview'});p.chooseRestaurant({detail:{value:0}});p.financePanel({currentTarget:{dataset:{panel:'withdrawals'}}});p.section({currentTarget:{dataset:{section:'orders'}}});assert.equal(p.data.restaurant.id,'r');assert.equal(p.data.accountPanel,'overview');assert.equal(p.data.section,'account');p.onHide();assert.equal(p.data.account,null);assert.equal(p.data.detail,null)
})
test('center retains order read error and permits explicit retry, images fall back without replacing others',async()=>{
 const p=ready(),old=api.get;p.setData({products:[{id:'p',imageUrl:'image'},{id:'q',imageUrl:'other'}]});p.imageError({currentTarget:{dataset:{id:'p'}}});assert.equal(p.data.products[0].imageUrl,'');assert.equal(p.data.products[1].imageUrl,'other');api.get=async()=>{throw new Error('读取失败')};try{await p.openOrder({currentTarget:{dataset:{id:'o'}}});assert.equal(p.data.detailError,'读取失败');assert.equal(p.data.detailLoading,false);api.get=async()=>({id:'o',status:'PAID',payableFen:0});await p.openOrder({currentTarget:{dataset:{}}});assert.equal(p.data.detail.id,'o')}finally{api.get=old}
})
test('center finance panels reflect selected tab and server totals without false payout',async()=>{
 const p=ready(),old=api.get;p.setData({section:'account'});api.get=async()=>({availableFen:100,pendingFen:200,heldFen:0,payoutEnabled:false,incomes:[],withdrawals:[{id:'w',amountFen:100,status:'APPROVED'}]});try{await p.loadSection();assert.equal(p.data.account.available,'¥1');assert.equal(p.data.account.withdrawals[0].statusLabel,'审核通过，待打款');p.financePanel({currentTarget:{dataset:{panel:'withdrawals'}}});assert.equal(p.data.accountPanel,'withdrawals');p.financePanel({currentTarget:{dataset:{panel:'unsupported'}}});assert.equal(p.data.accountPanel,'withdrawals')}finally{api.get=old}
})
test('center clears previous member restaurant identity before slow reload but keeps same-member selection',async()=>{
 const p=ready(),old=api.get,slow=deferred();p.setData({restaurants:[{id:'r',name:'旧店',address:'旧地址'}],restaurant:{id:'r',name:'旧店',address:'旧地址'},sections:view.sections({permissions:['finance']})});api.get=()=>slow.promise
 try{p.onHide();storage.set('mall.memberToken','new-member');const loading=p.onShow();assert.equal(p.data.restaurant,null);assert.deepEqual(p.data.restaurants,[]);assert.deepEqual(p.data.sections,[]);slow.resolve({items:[]});await loading
 const same=ready();same.setData({restaurants:[{id:'r'}],restaurant:{id:'r'},sections:view.sections({permissions:['orders']})});const again=deferred();api.get=()=>again.promise;same.onHide();const reload=same.onShow();assert.equal(same.data.restaurant.id,'r');again.resolve({items:[]});await reload
 }finally{api.get=old}
})
