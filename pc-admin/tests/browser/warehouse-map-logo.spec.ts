import {test,expect} from '@playwright/test'
import {account,ok,imageBytes} from './fixtures'
const assetId='11111111-2222-4333-8444-555555555555'
const initial={id:'s1',name:'深圳1号仓',contactName:'测试人员',contactPhone:'13800138000',address:'北山道附近',city:'深圳',latitude:22.597235,longitude:114.252243,openingHours:'09:00–23:00',enabled:true,acceptingOrders:true,supportedModes:['PICKUP','DELIVERY','EXPRESS'],deliveryRadiusMeters:3000,deliveryFeeFen:500,revision:1,logoAssetId:null as string|null,logoUrl:''}
test('map confirmation and square Logo upload persist on warehouse reload',async({page},info)=>{
 let saved={...initial}
 let uploads=0
 await page.addInitScript(()=>{
  class FakeMap {constructor(container:HTMLElement){container.textContent='地图交互测试';container.style.display='grid';container.style.placeItems='center'}on(){}destroy(){}setCenter(){}setCity(){}add(){}}
  class FakeMarker {on(){}setMap(){}setPosition(){}}
  Object.assign(window,{AMap:{Map:FakeMap,Marker:FakeMarker}})
 })
 await page.route('https://webapi.amap.com/maps*',route=>{const callback=new URL(route.request().url()).searchParams.get('callback');return route.fulfill({contentType:'application/javascript',body:callback?`window[${JSON.stringify(callback)}]();`:''})})
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname,method=route.request().method()
  if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:[...account.permissionCodes,'stores.read','stores.manage']})
  if(path.endsWith('/csrf'))return ok(route,{})
  if(path.endsWith('/staff'))return ok(route,{items:[]})
  if(path.endsWith('/map-runtime'))return ok(route,{jsApiKey:'synthetic-browser-key',securityProxyUrl:'/api/v1/admin/stores/amap-proxy/_AMapService'})
  if(path.endsWith('/map-search'))return ok(route,{items:[{name:'悦千山雅园',address:'广东省深圳市盐田区悦千山雅园',city:'深圳市',latitude:22.601234,longitude:114.251234}]})
  if(path.endsWith('/stores/s1')){if(method==='PATCH'){const fields=route.request().postDataJSON();expect(fields).not.toHaveProperty('logoUrl');saved={...saved,...fields,revision:saved.revision+1,logoUrl:fields.logoAssetId?`/api/v1/admin/assets/${assetId}/file`:''}}return ok(route,saved)}
  if(path.endsWith('/assets')&&method==='POST'){uploads++;expect(route.request().headers()['content-type']).toContain('multipart/form-data');return ok(route,{assetId,kind:'IMAGE',width:400,height:400,byteSize:1000,contentType:'image/png',adminUrl:`/api/v1/admin/assets/${assetId}/file`})}
  if(path.endsWith('/file'))return route.fulfill({body:imageBytes,contentType:'image/png'})
  return route.fulfill({status:404,json:{success:false,error:{code:'TEST_ROUTE_MISSING',message:path}}})
 })
 await page.goto('/stores/s1')
 await expect(page.getByRole('heading',{name:'深圳1号仓',exact:true})).toBeVisible()
 await page.getByLabel('搜索前置仓位置').fill('悦千山雅园')
 await page.getByRole('button',{name:'搜索地图',exact:true}).click()
 await page.getByRole('button',{name:/悦千山雅园.*广东省/}).click()
 await expect(page.getByLabel('详细地址',{exact:true})).toHaveValue(initial.address)
 await page.getByRole('button',{name:'使用此位置',exact:true}).click()
 await expect(page.getByLabel('详细地址',{exact:true})).toHaveValue('广东省深圳市盐田区悦千山雅园')
 await expect(page.getByLabel('经度',{exact:true})).toHaveValue('114.251234')
 await page.locator('input[type=file]').setInputFiles({name:'logo.png',mimeType:'image/png',buffer:imageBytes})
 const dialog=page.getByRole('dialog',{name:'裁切正方形 Logo'})
 await expect(dialog).toBeVisible()
 await expect(dialog).toHaveCSS('opacity','1')
 await page.screenshot({path:info.outputPath('logo-crop.png'),fullPage:false,animations:'disabled'})
 await dialog.getByRole('button',{name:'确认裁切并上传',exact:true}).click()
 await expect(dialog).not.toBeVisible()
 expect(uploads).toBe(1)
 await page.getByRole('button',{name:'保存前置仓',exact:true}).click()
 await expect(page.getByText('前置仓资料已保存。',{exact:true})).toBeVisible()
 expect(saved.logoAssetId).toBe(assetId)
 expect(saved.longitude).toBe(114.251234)
 await page.reload()
 await expect(page.getByAltText('当前前置仓 Logo')).toHaveAttribute('src',saved.logoUrl)
 await expect(page.getByLabel('详细地址',{exact:true})).toHaveValue(saved.address)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.screenshot({path:info.outputPath('warehouse-map-logo.png'),fullPage:true})
})
