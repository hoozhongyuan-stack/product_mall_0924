import {test,expect} from '@playwright/test'
import {ok,account} from './fixtures'
const store={id:'s1',name:'湘江路店',contactName:'张店长',contactPhone:'13800138000',address:'湘江路12号',city:'长沙',latitude:28,longitude:112,openingHours:'09:00–21:00',enabled:true,acceptingOrders:true,supportedModes:['PICKUP','DELIVERY','EXPRESS'],deliveryRadiusMeters:3000,deliveryFeeFen:500,revision:1}
const balances={pendingFen:null,availableFen:null,frozenFen:null,paidFen:null}
test('store management uses horizontal secondary navigation and real editor interactions',async({page},testInfo)=>{
 let saved={...store}
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:[...account.permissionCodes,'stores.read','stores.manage','stores.accounts.read']});if(path.endsWith('/csrf'))return ok(route,{})
  if(path.endsWith('/stores/settlement-settings')){if(route.request().method()==='PUT'){expect(route.request().postDataJSON()).toEqual({expectedRevision:1,receivedWindowDays:10});settlement={...settlement,receivedWindowDays:10,revision:2}}return ok(route,settlement)};if(path.endsWith('/staff'))return ok(route,{items:[]});if(path.endsWith('/stores/accounts'))return ok(route,{items:[{storeId:'s1',storeName:store.name,settlementReady:false,withdrawalReady:false,reason:'结算规则尚未配置',balance:balances}],total:1});if(path.endsWith('/account'))return ok(route,{storeId:'s1',storeName:store.name,settlementReady:false,withdrawalReady:false,reason:'结算规则尚未配置',balance:balances,income:[],withdrawals:[]});if(path.endsWith('/stores/s1')){if(route.request().method()==='PATCH')saved={...saved,...route.request().postDataJSON(),revision:2};return ok(route,saved)}if(path.endsWith('/stores'))return ok(route,{items:[saved],total:1});return route.fulfill({status:404,json:{success:false,error:{code:'TEST_ROUTE_MISSING',message:path}}})})
 await page.goto('/stores');await expect(page.getByRole('heading',{name:'门店管理',exact:true})).toBeVisible()
 const nav=page.getByRole('navigation',{name:'门店功能'});await expect(nav.getByRole('link')).toHaveCount(3);const positions=await nav.getByRole('link').evaluateAll(elements=>elements.map(e=>e.getBoundingClientRect().y));expect(Math.max(...positions)-Math.min(...positions)).toBeLessThan(2)
 await page.getByRole('link',{name:'编辑 / 人员授权'}).click();await expect(page.getByRole('heading',{name:'湘江路店',exact:true})).toBeVisible();await page.getByLabel('门店名称',{exact:true}).fill('湘江路旗舰店');await page.getByRole('button',{name:'保存门店',exact:true}).click();await expect(page.getByText('门店资料已保存。',{exact:true})).toBeVisible();await page.reload();await expect(page.getByLabel('门店名称',{exact:true})).toHaveValue('湘江路旗舰店')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.screenshot({path:testInfo.outputPath('store-editor.png'),fullPage:true})
 await nav.getByRole('link',{name:'门店账户'}).click();await page.getByRole('link',{name:'账户详情',exact:true}).click();await expect(page.getByText('结算规则尚未配置',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'提现待配置'})).toBeDisabled();await page.screenshot({path:testInfo.outputPath('store-account.png'),fullPage:true})
})

test('map settings provide masked configuration and confirmation without retaining secrets', async ({page}, testInfo) => {
 const config={revision:1,managed:false,source:'ENV',encryptionReady:true,available:true,webServiceKey:{configured:true,tail:'1234'},jsApiKey:{configured:false,tail:''},jsSecurityCode:{configured:false,tail:''}}
 let saved={...config}
 let settlement={receivedWindowDays:7,revision:1,appliesTo:'NEW_ORDERS'}
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:[...account.permissionCodes,'stores.manage']})
  if(path.endsWith('/csrf'))return ok(route,{})
  if(path.endsWith('/stores/settlement-settings')){if(route.request().method()==='PUT'){expect(route.request().postDataJSON()).toEqual({expectedRevision:1,receivedWindowDays:10});settlement={...settlement,receivedWindowDays:10,revision:2}}return ok(route,settlement)}
  if(path.endsWith('/auth/confirm')){expect(route.request().postDataJSON()).toMatchObject(route.request().postDataJSON().action==='stores.map.configure'?{action:'stores.map.configure',objectId:'amap',revision:1}:{action:'stores.settlement.configure',objectId:'aftersale-policy',revision:1});return ok(route,{confirmationToken:'synthetic-confirmation'})}
  if(path.endsWith('/stores/map-settings')){
   if(route.request().method()==='PUT'){
    expect(route.request().postDataJSON()).toEqual({expectedRevision:1,webServiceKey:'synthetic-web-key',jsApiKey:'',jsSecurityCode:''})
    saved={...config,revision:2,managed:true,source:'MANAGED'}
   }
   return ok(route,saved)
  }
  return route.fulfill({status:404,json:{success:false,error:{code:'TEST_ROUTE_MISSING',message:'测试路由未配置'}}})
 })
 await page.goto('/stores/map-settings')
 await expect(page.getByRole('heading',{name:'门店设置',exact:true})).toBeVisible()
 await page.locator('#amap-webServiceKey').fill('synthetic-web-key')
 await page.getByRole('button',{name:'保存配置',exact:true}).click()
 await page.getByLabel('当前账号密码',{exact:true}).fill('synthetic-password')
 await page.getByRole('button',{name:'确认保存',exact:true}).click()
 await expect(page.getByText('配置已保存。',{exact:false})).toBeVisible()
 await expect(page.locator('#amap-webServiceKey')).toHaveValue('')
 await page.reload();await expect(page.getByText('配置来源：后台托管 · 修订 2')).toBeVisible()
 await page.locator('#store-aftersale-days').fill('10')
 await page.getByRole('button',{name:'保存售后期',exact:true}).click()
 await page.locator('#store-settlement-password').fill('synthetic-password')
 await page.getByRole('button',{name:'确认保存售后期',exact:true}).click()
 await expect(page.getByText('售后期已保存，仅新订单生效。')).toBeVisible()
 await page.reload();await expect(page.locator('#store-aftersale-days')).toHaveValue('10')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
 await page.screenshot({path:testInfo.outputPath('map-settings.png'),fullPage:true})
})

test('store finance config and offline payout remain separate and persist after reload',async({page},testInfo)=>{
 let rule={skuId:'sku-1',productName:'老挝啤酒',specKey:'synthetic-axis-id:synthetic-option-id',specLabel:'330ml / 箱',skuCode:'BEER-BOX',saleUnit:'箱',purchaseCostFen:null as number|null,platformShareBps:null as number|null,revision:0}
 let withdrawal={id:'w1',amountFen:8200,payeeName:'测试店长',bankName:'测试银行',bankAccount:'****1234',status:'PENDING_REVIEW',revision:1,reason:'',createdAt:'2026-10-10T10:00:00Z',paidAt:null as string|null,paymentReference:''}
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname,method=route.request().method();if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:[...account.permissionCodes,'stores.manage','stores.accounts.read','stores.accounts.manage']});if(path.endsWith('/csrf'))return ok(route,{});if(path.endsWith('/auth/confirm'))return ok(route,{confirmationToken:'synthetic-confirmation'});if(path.endsWith('/map-settings'))return ok(route,{revision:1,managed:true,source:'MANAGED',encryptionReady:true,available:true,webServiceKey:{configured:true,tail:'1234'},jsApiKey:{configured:false,tail:''},jsSecurityCode:{configured:false,tail:''}});if(path.endsWith('/settlement-settings'))return ok(route,{receivedWindowDays:7,revision:1,appliesTo:'NEW_ORDERS'});if(path.endsWith('/profit-rules'))return ok(route,{items:[rule],total:1});if(path.endsWith('/profit-rules/sku-1')&&method==='PUT'){expect(route.request().postDataJSON()).toEqual({expectedRevision:0,purchaseCostFen:6000,platformShareBps:2000});rule={...rule,purchaseCostFen:6000,platformShareBps:2000,revision:1};return ok(route,rule)};if(path.endsWith('/withdrawals/w1/review')){expect(route.request().postDataJSON().decision).toBe('APPROVE');withdrawal={...withdrawal,status:'APPROVED_PENDING_PAYMENT',revision:2};return ok(route,withdrawal)};if(path.endsWith('/withdrawals/w1/pay')){expect(route.request().postDataJSON().paymentReference).toBe('银行流水-001');withdrawal={...withdrawal,status:'PAID',revision:3,paidAt:'2026-10-10T11:00:00Z',paymentReference:'银行流水-001'};return ok(route,withdrawal)};if(path.endsWith('/account'))return ok(route,{storeId:'s1',storeName:'湘江路店',settlementReady:true,withdrawalReady:true,reason:'',balance:{pendingFen:0,availableFen:0,frozenFen:withdrawal.status==='PAID'?0:8200,paidFen:withdrawal.status==='PAID'?8200:0},income:[{id:'i1',orderId:'o1',orderNo:'ORD001',paidFen:10000,costFen:6000,platformFen:800,freightFen:1000,storeFen:8200,status:'SETTLED',reason:'',availableAt:null,settledAt:null}],withdrawals:[withdrawal]});return route.fulfill({status:404,json:{success:false,error:{code:'TEST_ROUTE_MISSING',message:path}}})})
 await page.goto('/stores/map-settings');await page.getByRole('button',{name:'设置分润',exact:true}).click();await page.locator('#store-purchase-cost').fill('60');await page.locator('#store-platform-share').fill('20');await page.getByRole('button',{name:'保存规格分润',exact:true}).click();await page.locator('#store-profit-password').fill('synthetic-password');await page.getByRole('button',{name:'确认保存分润',exact:true}).click();await expect(page.getByText('规格分润规则已保存，仅影响后续订单；已有订单保持原快照。',{exact:true})).toBeVisible();await page.reload();await expect(page.getByText('¥60.00 / 每箱',{exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.goto('/stores/accounts/s1');await page.getByRole('button',{name:'审核通过',exact:true}).click();await page.locator('#withdrawal-password-w1').fill('synthetic-password');await page.getByRole('button',{name:'确认审核通过',exact:true}).click();await expect(page.getByRole('button',{name:'登记线下发放',exact:true})).toBeVisible();await expect(page.getByText('¥82.00 · 审核通过，待线下发放',{exact:true})).toBeVisible();await page.getByRole('button',{name:'登记线下发放',exact:true}).click();await page.locator('#withdrawal-reference-w1').fill('银行流水-001');await page.locator('#withdrawal-password-w1').fill('synthetic-password');await page.getByRole('button',{name:'确认登记线下发放',exact:true}).click();await expect(page.getByText('¥82.00 · 已线下发放',{exact:true})).toBeVisible();await page.reload();await expect(page.getByText('¥82.00 · 已线下发放',{exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:testInfo.outputPath('store-finance-paid.png'),fullPage:true})
})
