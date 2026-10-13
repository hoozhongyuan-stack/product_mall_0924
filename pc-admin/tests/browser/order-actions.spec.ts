import { expect, test, type Page, type Route } from '@playwright/test'
import { account, installMockApi, ok, screenshot } from './fixtures'

const permissions=['order.read','payment.offline.confirm','fulfillment.ship','fulfillment.redeem','aftersale.read']
const baseOrder=()=>({orderId:'qa-order',orderNo:'QA20261011',revision:3,status:'PAID',paymentMethod:'OFFLINE',paymentReviewStatus:'CONFIRMED',paymentInstructions:{merchantAccountId:'BANK',instructions:'仅用于隔离模拟验证',revision:1,timeoutMinutes:30},goodsTotalFen:10000,shippingFeeFen:0,couponDiscountFen:0,pointsDiscountFen:0,payableFen:10000,createdAt:'2026-10-11T01:00:00Z',expiresAt:'2026-10-11T02:00:00Z',paidAt:'2026-10-11T01:10:00Z',closedAt:null,closeReason:null,deliveryMode:'DELIVERY',fulfillmentStatus:'WAITING_DELIVERY',storeName:'锁定前置仓',fulfillmentWarehouse:{id:'warehouse-1',name:'原履约仓库'},dining:{restaurantId:'dining-1',name:'测试餐饮店',tableNumber:'A-01',address:'餐饮店固定地址'},address:null,shipment:null,items:[{orderLineId:'line-1',name:'模拟商品',skuCode:'QA-SKU',quantity:2,saleUnit:'瓶',fulfillmentKind:'SHIP',goodsAmountFen:10000,payableFen:10000,fulfillment:{status:'WAITING_SHIPMENT',refundedQuantity:0,remainingQuantity:2}}]})
type RequestRecord={path:string;body:any;key:string|undefined}
async function fixture(page:Page,initial:any,handler:(route:Route,path:string,order:any,records:RequestRecord[])=>Promise<boolean>){
 let current=initial
 const records:RequestRecord[]=[]
 const verify=await installMockApi(page,async(route,path)=>{
  if(path==='/api/v1/admin/me'){await ok(route,{...account,permissionCodes:permissions});return true}
  if(path==='/api/v1/admin/orders'){await ok(route,{items:[current],total:1,page:1,pageSize:20});return true}
  if(path==='/api/v1/admin/orders/qa-order'){await ok(route,current);return true}
  if(path==='/api/v1/admin/fulfillment/carriers'){await ok(route,{items:[{code:'SF',name:'顺丰',enabled:true,revision:1}]});return true}
  if(route.request().method()==='POST'&&!path.endsWith('/csrf')){
   expect(path).not.toContain('/auth/confirm')
   expect(route.request().headers()['x-action-confirmation']).toBeUndefined()
   records.push({path,body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key']})
  }
  return handler(route,path,current,records)
 })
 await page.goto('/orders')
 await expect(page.getByRole('heading',{name:'订单管理',exact:true})).toBeVisible()
 return{records,verify,setOrder:(next:any)=>{current=next}}
}
async function open(page:Page,label:string){await page.getByRole('button',{name:label,exact:true}).click();const dialog=page.getByRole('dialog',{name:label,exact:true});await expect(dialog).toBeVisible();await expect(dialog.getByText('正在读取最新订单…')).toBeHidden();await expect(dialog.locator('input[type=password]')).toHaveCount(0);return dialog}

// This suite uses intercepted HTTP only; no UAT or payment provider is contacted.
test('order dialog: confirms offline receipt without operator password',async({page},info)=>{
 const order={...baseOrder(),status:'PENDING_PAYMENT',fulfillmentStatus:'WAITING_PAYMENT',paidAt:null}
 const f=await fixture(page,order,async(route,path,current)=>{
  if(path.endsWith('/offline-reconciliations')){await ok(route,{reconciliationId:'qa-receipt'});return true}
  if(path.endsWith('/qa-receipt/confirm')){await ok(route,{outcome:'PAID',order:{...current,status:'PAID',revision:4,fulfillmentStatus:'WAITING_PREPARATION'}});return true}
  return false
 })
 const dialog=await open(page,'确认收款')
 await expect(dialog).toContainText('测试餐饮店');await expect(dialog).toContainText('A-01');await expect(dialog).toContainText('原履约仓库')
 await dialog.getByLabel('实际到账流水号',{exact:true}).fill('BANK-QA-01')
 await dialog.getByLabel('实际到账时间',{exact:true}).fill('2026-10-11T10:00')
 await dialog.getByLabel('已核对实际账户、流水、金额和到账时间',{exact:true}).check()
 await screenshot(page,info,'receipt-confirmation')
 await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toBeHidden()
 expect(f.records.map(r=>r.path)).toEqual(['/api/v1/admin/orders/qa-order/offline-reconciliations','/api/v1/admin/payments/offline-reconciliations/qa-receipt/confirm'])
 expect(f.records[0].body).toMatchObject({amountFen:10000,externalTradeNo:'BANK-QA-01',expectedRevision:3,verified:true})
 expect(f.records[1].key).toBe(f.records[0].key);f.verify()
})

test('platform delivery and pickup are visible without warehouse operation buttons',async({page},info)=>{
 const f=await fixture(page,baseOrder(),async()=>false)
 await expect(page.getByRole('button',{name:'开始配送',exact:true})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'确认备货完成',exact:true})).toHaveCount(0)
 await page.getByRole('button',{name:'查看详情',exact:true}).click()
 await expect(page.getByRole('dialog',{name:'订单详情',exact:true})).toContainText('由对应前置仓中心备货和交付')
 await page.locator('.el-overlay').last().evaluate(async root=>{await Promise.all(root.getAnimations({subtree:true}).map(animation=>animation.finished))})
 await page.screenshot({path:info.outputPath('platform-local-readonly.png'),fullPage:false,animations:'disabled'})
 await page.getByRole('button',{name:'关闭',exact:true}).click()
 f.setOrder({...baseOrder(),deliveryMode:'PICKUP',fulfillmentStatus:'WAITING_PICKUP'});await page.getByRole('button',{name:'刷新订单',exact:true}).click()
 await expect(page.getByRole('button',{name:'确认自提',exact:true})).toHaveCount(0)
 expect(f.records).toHaveLength(0);f.verify()
})

test('platform virtual redemption shares frozen scope and leaves held rights untouched',async({page},info)=>{
 const original=baseOrder();const progress={status:'PARTIAL',redeemScope:'BOTH',redeemStoreIds:['store-1'],redeemStores:[{storeId:'store-1',name:'授权深圳前置仓'}],redeemedQuantity:2,remainingQuantity:3,availableQuantity:2,frozenQuantity:1,canRedeemPlatform:true,validUntil:'2028-01-01'}
 const order={...original,deliveryMode:'REDEEM',fulfillmentOwner:'REDEEM',fulfillmentStatus:'WAITING_REDEMPTION',redeemEligible:true,dining:null,inventoryWarehouse:null,items:[{...original.items[0],quantity:5,fulfillmentKind:'REDEEM',fulfillment:progress}]}
 let f:Awaited<ReturnType<typeof fixture>>
 f=await fixture(page,order,async(route,path,current)=>{
  if(path.endsWith('/redemptions/lookup')){await ok(route,{...progress,voucherId:'voucher-1',orderId:'qa-order',orderNo:current.orderNo,orderLineId:'line-1',name:'服务次卡',quantity:5,status:'READY',revision:4});return true}
  if(path.endsWith('/voucher-1/use')){const next={...current,revision:4,redeemEligible:false,items:[{...current.items[0],fulfillment:{...progress,redeemedQuantity:4,remainingQuantity:1,availableQuantity:0}}]};f.setOrder(next);await ok(route,{order:next});return true}return false
 })
 const dialog=await open(page,'核销');await dialog.getByLabel('顾客出示的核销凭证',{exact:true}).fill('CARD-QA');await dialog.getByRole('button',{name:'读取凭证',exact:true}).click()
 await expect(dialog).toContainText('平台＋指定前置仓：授权深圳前置仓');await expect(dialog).toContainText('售后冻结 1')
 await dialog.getByLabel('本次核销数量',{exact:true}).fill('3');await expect(dialog.locator('[data-test=action-submit]')).toBeDisabled()
 await dialog.getByLabel('本次核销数量',{exact:true}).fill('2');await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toBeHidden()
 const writes=f.records.filter(r=>r.path.endsWith('/use'));expect(writes).toHaveLength(1);expect(writes[0].body).toEqual({quantity:2,expectedRevision:4});expect(writes[0].key).toMatch(/^[a-f0-9-]{36}$/)
 await page.getByRole('button',{name:'查看详情',exact:true}).click();const detail=page.getByRole('dialog',{name:'订单详情',exact:true});await expect(detail).toContainText('剩余 1 · 售后冻结 1 · 当前可核销 0')
 await page.locator('.el-overlay').last().evaluate(async root=>{await Promise.all(root.getAnimations({subtree:true}).map(animation=>animation.finished))})
 await page.screenshot({path:info.outputPath('platform-shared-rights.png'),fullPage:false,animations:'disabled'});f.verify()
})

test('order dialog: frozen voucher cannot be redeemed',async({page},info)=>{
 const order={...baseOrder(),deliveryMode:'EXPRESS',fulfillmentStatus:'WAITING_REDEMPTION',items:[{...baseOrder().items[0],fulfillmentKind:'REDEEM',fulfillment:{status:'READY',refundedQuantity:0,remainingQuantity:2,availableQuantity:2,redeemScope:'PLATFORM',canRedeemPlatform:true}}]}
 const f=await fixture(page,order,async(route,path)=>{if(path.endsWith('/redemptions/lookup')){await ok(route,{voucherId:'voucher-1',orderId:'qa-order',orderNo:'QA20261011',orderLineId:'line-1',name:'模拟商品',quantity:2,redeemedQuantity:0,voidedQuantity:0,remainingQuantity:2,status:'FROZEN',revision:1,availableQuantity:0,validUntil:'2028-01-01T00:00:00Z'});return true}return false})
 const dialog=await open(page,'核销');await dialog.getByLabel('顾客出示的核销凭证',{exact:true}).fill('FROZEN-QA');await dialog.getByRole('button',{name:'读取凭证',exact:true}).click()
 await expect(dialog.locator('[data-test=action-submit]')).toBeDisabled();await screenshot(page,info,'frozen-voucher');expect(f.records).toHaveLength(1);f.verify()
})

test('order dialog: reload resumes the frozen receipt body and original request key',async({page},info)=>{
 const order={...baseOrder(),status:'PENDING_PAYMENT',fulfillmentStatus:'WAITING_PAYMENT',paidAt:null};let attempts=0
 const f=await fixture(page,order,async(route,path,current)=>{
  if(path.endsWith('/offline-reconciliations')){attempts++;if(attempts===1)await route.fulfill({status:503,json:{success:false,error:{code:'UNKNOWN',message:'模拟网络中断'}}});else await ok(route,{reconciliationId:'qa-recovery'});return true}
  if(path.endsWith('/qa-recovery/confirm')){await ok(route,{outcome:'PAID',order:{...current,status:'PAID',revision:4,fulfillmentStatus:'WAITING_PREPARATION'}});return true}return false
 })
 let dialog=await open(page,'确认收款');await dialog.getByLabel('实际到账流水号',{exact:true}).fill('ORIGINAL-QA');await dialog.getByLabel('实际到账时间',{exact:true}).fill('2026-10-11T10:00');await dialog.getByLabel('已核对实际账户、流水、金额和到账时间',{exact:true}).check()
 await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toContainText('操作结果待确认');await expect(dialog.locator('[data-test=action-submit]')).toBeDisabled()
 await page.reload();await page.getByRole('button',{name:'核查确认收款结果',exact:true}).click();dialog=page.getByRole('dialog',{name:'确认收款',exact:true})
 await expect(dialog.getByLabel('实际到账流水号',{exact:true})).toHaveValue('ORIGINAL-QA');await expect(dialog.getByLabel('实际到账流水号',{exact:true})).toBeDisabled()
 await dialog.getByRole('button',{name:'查询订单结果',exact:true}).click();await expect(dialog.locator('[data-test=action-submit]')).toBeEnabled();await screenshot(page,info,'receipt-recovery')
 await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toBeHidden()
 expect(f.records[1].key).toBe(f.records[0].key);expect(f.records[1].body).toEqual(f.records[0].body);expect(attempts).toBe(2);f.verify()
})
