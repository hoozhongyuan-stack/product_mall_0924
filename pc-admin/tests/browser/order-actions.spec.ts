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

test('order dialog: dispatches dining delivery with the original warehouse',async({page},info)=>{
 const f=await fixture(page,baseOrder(),async(route,path,current)=>{if(path.endsWith('/local-fulfillment')){await ok(route,{order:{...current,fulfillmentStatus:'DELIVERING',revision:4}});return true}return false})
 const dialog=await open(page,'开始配送')
 await dialog.getByLabel('配送人员',{exact:true}).fill('测试配送员');await dialog.getByLabel('联系电话',{exact:true}).fill('13800000000')
 await dialog.getByLabel('操作备注（选填）',{exact:true}).fill('配送到餐厅A-01桌')
 await screenshot(page,info,'dispatch-confirmation');await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toBeHidden()
 expect(f.records[0].body).toEqual({action:'DISPATCH',expectedRevision:3,note:'配送人员：测试配送员；联系电话：13800000000；配送到餐厅A-01桌'})
 await expect(page.getByRole('button',{name:'确认送达',exact:true})).toBeVisible();f.verify()
})

test('order dialog: verifies pickup code before completing pickup',async({page},info)=>{
 const order={...baseOrder(),deliveryMode:'PICKUP',fulfillmentStatus:'WAITING_PICKUP'}
 const f=await fixture(page,order,async(route,path,current)=>{if(path.endsWith('/pickup-preview')){await ok(route,{verified:true,revision:3});return true}if(path.endsWith('/local-fulfillment')){await ok(route,{order:{...current,fulfillmentStatus:'COMPLETED',revision:4}});return true}return false})
 const dialog=await open(page,'确认自提');await expect(dialog.locator('[data-test=action-submit]')).toBeDisabled()
 await dialog.getByLabel('自提凭证',{exact:true}).fill('PICKUP-QA');await dialog.getByRole('button',{name:'核验自提凭证'}).click()
 await expect(dialog.getByText('凭证核验通过',{exact:false})).toBeVisible();await screenshot(page,info,'pickup-preview')
 await dialog.locator('[data-test=action-submit]').click();await expect(dialog).toBeHidden()
 expect(f.records.at(-1)!.body).toEqual({action:'COMPLETE',expectedRevision:3,note:'',pickupCode:'PICKUP-QA'});f.verify()
})

test('order dialog: frozen voucher cannot be redeemed',async({page},info)=>{
 const order={...baseOrder(),deliveryMode:'EXPRESS',fulfillmentStatus:'WAITING_REDEMPTION',items:[{...baseOrder().items[0],fulfillmentKind:'REDEEM',fulfillment:{status:'READY',refundedQuantity:0,remainingQuantity:2}}]}
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
