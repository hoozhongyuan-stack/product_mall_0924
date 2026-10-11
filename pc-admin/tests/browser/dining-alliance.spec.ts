import {test,expect} from '@playwright/test'
import {ok,account} from './fixtures'
const restaurant={id:'r1',name:'湘江餐饮',address:'深圳测试街1号',latitude:22.5,longitude:114.1,contactName:'张先生',contactPhone:'13800000000',enabled:true,revision:1,tableCount:1,fulfillmentStore:{id:'s1',name:'南山前置仓'}}
test('restaurant admin shows real states and retains conflicting draft on desktop and narrow screen',async({page},info)=>{
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('/me'))return ok(route,{...account,permissionCodes:['dining.read','dining.manage','dining.finance.read']});if(path.endsWith('/csrf'))return ok(route,{});if(path.endsWith('/tables'))return ok(route,{items:[{id:'t1',number:'A03',enabled:true,revision:1}],total:1});if(path.endsWith('/staff'))return ok(route,{items:[],total:0});if(path.endsWith('/restaurants/r1')){if(route.request().method()==='PATCH')return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({success:false,error:{code:'REVISION_CONFLICT',message:'餐饮店已修改'}})});return ok(route,restaurant)}if(path.endsWith('/restaurants'))return ok(route,{items:[restaurant],total:1});return ok(route,{})})
 await page.goto('/dining-alliance')
 await expect(page.getByRole('heading',{name:'餐饮联盟店',exact:true})).toBeVisible()
 await expect(page.getByText('南山前置仓',{exact:true})).toBeVisible()
 await page.getByRole('link',{name:'湘江餐饮',exact:true}).click()
 await expect(page.getByText('A03',{exact:true})).toBeVisible()
 await page.getByRole('textbox',{name:'餐饮店名称',exact:true}).fill('修改后的餐饮店')
 await page.getByRole('button',{name:'保存餐饮店',exact:true}).click()
 await expect(page.getByText(/已保留编辑草稿/)).toBeVisible()
 await expect(page.getByRole('textbox',{name:'餐饮店名称',exact:true})).toHaveValue('修改后的餐饮店')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
 await page.evaluate(()=>window.scrollTo(0,0))
 await page.screenshot({path:`test-results/browser/dining-alliance-${info.project.name}.png`,fullPage:true})
})
