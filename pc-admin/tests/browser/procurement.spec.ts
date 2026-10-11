import { test, expect } from '@playwright/test'
import { ok, account } from './fixtures'
const id='11a27082-c544-4ed8-8b70-3c1070bf560b'
const original={id,orderNo:'P20261011001',storeId:'s',storeName:'深圳1号仓',status:'REVIEW',revision:1,goodsAmountFen:6000,freightFen:500,payableFen:6500,createdAt:'2026-10-11T01:00:00Z',paymentInstructions:'银行转账，备注进货订单号',paymentProof:null,shipment:null,lines:[{id:'line',skuId:'sku',productName:'商务双瓶礼盒',specLabel:'2瓶装 / 500ml',saleUnit:'盒',quantity:1,purchaseCostFen:6000,conversionRatio:2,baseUnit:'瓶',unitVersionId:'unit'}],records:[]}
test('procurement list and financial dialogs stay usable on desktop and narrow screens',async({page},info)=>{
 let order={...original}, instructions='开户行：示例银行\n付款请备注进货订单号', revision=1
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:[...account.permissionCodes,'stores.procurement.read','stores.procurement.manage','stores.procurement.finance']})
  if(path.endsWith('/csrf'))return ok(route,{})
  if(path.endsWith('/auth/confirm'))return ok(route,{confirmationToken:'confirm',expiresAt:'2030-01-01T00:00:00Z'})
  if(path.endsWith('/procurement/settings')){
   if(route.request().method()==='PUT'){instructions=route.request().postDataJSON().paymentInstructions;revision++}
   return ok(route,{paymentInstructions:instructions,revision})
  }
  if(path.endsWith('/procurement/orders'))return ok(route,{items:[order],total:1,page:1,pageSize:20})
  if(path.endsWith(`/orders/${id}`))return ok(route,order)
  if(path.endsWith('/review')){
   const body=route.request().postDataJSON();expect(body).toMatchObject({expectedRevision:1,freightFen:850});expect(route.request().headers()['idempotency-key']).toBe(body.requestKey)
   order={...order,status:'PROOF',revision:2,freightFen:850,payableFen:6850};return ok(route,order)
  }
  if(path.endsWith('/confirm-payment')){expect(route.request().postDataJSON()).toMatchObject({expectedRevision:2,paymentReference:'BANK-2026'});order={...order,status:'PAID',revision:3};return ok(route,order)}
  if(path.endsWith('/ship')){expect(route.request().postDataJSON()).toMatchObject({carrier:'顺丰',tracking:'SF2026'});order={...order,status:'SHIPPED',revision:4};return ok(route,order)}
  return ok(route,{items:[],total:0})
 })
 await page.goto('/stores/procurement')
 await expect(page.getByRole('heading',{name:'前置仓进货订单'})).toBeVisible()
 await page.screenshot({path:info.outputPath(`procurement-list-${info.project.name}.png`),fullPage:true})
 await page.getByRole('link',{name:'查看详情'}).click()
 await page.getByRole('button',{name:'审单并确认运费',exact:true}).click()
 await page.getByLabel('运费（元）').fill('8.50')
 await page.screenshot({path:info.outputPath(`procurement-review-${info.project.name}.png`),fullPage:true})
 await page.getByRole('dialog',{name:'审单并确认运费'}).getByRole('button',{name:'确认提交'}).click()
 await page.getByRole('button',{name:'确认操作',exact:true}).click()
 await expect(page.getByRole('dialog',{name:'审单并确认运费'})).not.toBeVisible()
 await page.getByRole('button',{name:'确认实际到账',exact:true}).click()
 await page.getByLabel('到账凭证编号').fill('BANK-2026');await page.getByLabel('管理员密码').fill('one-run')
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await page.getByRole('button',{name:'确认操作',exact:true}).click()
 await expect(page.getByRole('dialog',{name:'确认实际到账'})).not.toBeVisible()
 await page.getByRole('button',{name:'填写物流并发货',exact:true}).click();await page.getByLabel('物流公司').fill('顺丰');await page.getByLabel('物流单号').fill('SF2026')
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await page.getByRole('button',{name:'确认操作',exact:true}).click()
 await expect(page.getByRole('dialog',{name:'填写物流并发货'})).not.toBeVisible()
 await page.goto('/stores/procurement/settings');await page.getByLabel('收款说明', {exact:true}).fill('请备注采购单号');await page.getByLabel('管理员密码').fill('one-run');await page.getByRole('button',{name:'保存收款说明'}).click()
 await expect(page.getByRole('status')).toContainText('已保存');await page.reload();await expect(page.getByLabel('收款说明',{exact:true})).toHaveValue('请备注采购单号')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
})
