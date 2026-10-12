const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
const base='https://mall.test',id='10000000-0000-4000-8000-000000000001'
global.getApp=()=>({globalData:{apiBaseUrl:base}})
function mount(file){let def;global.Page=d=>{def=d};delete require.cache[require.resolve(file)];require(file);return {...def,data:structuredClone(def.data),setData(p){this.data={...this.data,...p}}}}
test('home initial surface is neutral and never renders premature store header or old content on refresh',async()=>{
 const stored=new Map([['mall.selectedStore.v1',{id,name:'旧仓'}]]);let reply;global.wx={getStorageSync:k=>stored.get(k),setStorageSync:(k,v)=>stored.set(k,v),request:r=>{reply=r},hideShareMenu(){},showShareMenu(){}}
 const page=mount('../pages/home/home.js');assert.equal(page.data.theme.headerBackgroundColor,'#FFFFFF');page.setData({store:{id},components:[{id:'old'}],state:'ready'});page.homeToken=0;const loading=page.loadHome();assert.deepEqual(page.data.components,[]);assert.equal(page.data.state,'loading');reply.success({statusCode:200,data:{success:true,data:{versionId:'new',config:{schemaVersion:4,theme:{headerBackgroundColor:'#A3202B'},components:[]}}}});await loading;assert.equal(page.data.state,'ready');assert.equal(page.data.versionId,'new');const markup=fs.readFileSync(path.join(__dirname,'../components/home-surface.wxml'),'utf8');assert.match(markup,/wx:if="{{state === 'ready'}}" class="home-header/);assert.match(markup,/home-skeleton/)
})
test('normal App launch opens home without startup redirect and preserves native explicit route',()=>{
 let app;global.App=value=>{app=value};global.wx={redirectTo:()=>assert.fail('normal launch must be direct')};delete require.cache[require.resolve('../app.js')];require('../app.js');app.onLaunch({path:'pages/product/detail',query:{productId:id}});assert.equal(require('../lib/startup').redirectIfPending(),false);const config=JSON.parse(fs.readFileSync(path.join(__dirname,'../app.json'),'utf8'));assert.equal(config.pages[0],'pages/home/home')
})
test('authoritative shipping presentation validates split and reflects frozen free-source without recalculating fees',()=>{
 const present=require('../lib/shipping-presentation');const snapshot={eligible:true,pending:false,message:'',costFen:1200,customerFeeFen:0,warehouseBearsFen:1200,platformBearsFen:0,freeSource:'WAREHOUSE'};const source={shippingFeeFen:0,shippingSnapshot:snapshot};assert.equal(present.shippingPresentation(source).shippingMessage,'前置仓包邮');assert.equal(present.shippingPresentation({...source,shippingSnapshot:{...snapshot,warehouseBearsFen:0,platformBearsFen:1200,freeSource:'PLATFORM'}}).shippingMessage,'平台活动包邮');assert.equal(present.shippingPresentation({...source,shippingSnapshot:{...snapshot,eligible:false,message:'地址位于排除区域'}}).shippingAllowed,false);assert.equal(present.shippingPresentation({...source,shippingSnapshot:{...snapshot,costFen:1300}}).shippingAllowed,false)
})
test('late store discovery after home hide cannot issue or paint published home',async()=>{
 let callback,requests=0;global.wx={getStorageSync:()=>null,request:r=>{requests++;if(requests===1)callback=r;else r.fail({})}};const page=mount('../pages/home/home.js');const task=page.onLoad();page.onHide();callback.success({statusCode:200,data:{success:true,data:{items:[]}}});await task;assert.equal(requests,1);assert.equal(page.data.state,'loading')
})
test('mixed courier templates permit partial operating free shipping without claiming the whole order is free',()=>{
 const present=require('../lib/shipping-presentation');const source={shippingFeeFen:800,shippingSnapshot:{eligible:true,pending:false,message:'',costFen:2000,customerFeeFen:800,warehouseBearsFen:1200,platformBearsFen:0,freeSource:'MIXED'}}
 assert.deepEqual(present.shippingPresentation(source),{shippingAllowed:true,shippingMessage:'部分商品包邮',shippingFreeSource:'MIXED'})
 const markup=fs.readFileSync(path.join(__dirname,'../components/commerce-amounts.wxml'),'utf8');assert.match(markup,/部分商品包邮/);assert.match(markup,/部分运费减免/)
})
