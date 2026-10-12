import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AssetBrowser from '../../src/shared/AssetBrowser.vue'
import AdminListPager from '../../src/shared/list/AdminListPager.vue'
import AdminCursorPager from '../../src/shared/list/AdminCursorPager.vue'
import CodeVersionsView from '../../src/views/CodeVersionsView.vue'
import InventorySkuSummary from '../../src/views/inventory/InventorySkuSummary.vue'
import HomePagePreview from '../../src/views/pages/HomePagePreview.vue'
import RestaurantsView from '../../src/views/dining-alliance/RestaurantsView.vue'
import StoreAccountsView from '../../src/views/stores/StoreAccountsView.vue'
import ProductManagement from '../../src/views/catalog/ProductManagement.vue'
import PointsExchangeView from '../../src/views/PointsExchangeView.vue'
import StaffPanel from '../../src/views/dining-alliance/StaffPanel.vue'
import FinanceView from '../../src/views/dining-alliance/FinanceView.vue'
import PageComponentEditor from '../../src/views/pages/PageComponentEditor.vue'
import { assetQuery, listMediaUrl, selectionError, selectionStillCurrent, validateAssetFile } from '../../src/shared/media-library'
import { api, type Account } from '../../src/api'
import type { LibraryAsset } from '../../src/shared/media-library'
import type { InventorySku } from '../../src/views/inventory/types'
import type { PageConfig } from '../../src/views/pages/types'
vi.mock('../../src/api', async original => ({...await original<typeof import('../../src/api')>(), api:vi.fn()}))
const wrappers: ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
const dialog={props:['modelValue'],template:'<div v-if="modelValue" role="dialog"><slot/><slot name="footer"/></div>'}
const button={template:'<button><slot/></button>'}
const global={stubs:{ElDialog:dialog,ElButton:button,ElInput:true,ElSelect:true,ElOption:true,AssetUploadQueue:true,CodeDomainCheck:true}}
const asset=(kind:'IMAGE'|'GIF'|'VIDEO', thumb=true):LibraryAsset=>({assetId:kind,kind,contentType:kind==='VIDEO'?'video/mp4':kind==='GIF'?'image/gif':'image/png',adminUrl:`/original/${kind}`,thumbnailUrl:thumb?`/thumbnail/${kind}`:undefined,byteSize:20000000,width:800,height:800,availability:'READY',originalName:kind,createdAt:'2026-10-12',sha256:'a',bindingStatus:'UNBOUND'})
it('list URLs never fall back to full images, animated GIF or videos',()=>{
 expect(listMediaUrl(asset('IMAGE'))).toBe('/thumbnail/IMAGE')
 expect(listMediaUrl(asset('GIF'))).toBe('/thumbnail/GIF')
 expect(listMediaUrl(asset('IMAGE',false))).toBe('')
 expect(listMediaUrl(asset('GIF',false))).toBe('')
 expect(listMediaUrl(asset('VIDEO'))).toBe('')
 expect(listMediaUrl(null)).toBe('')
})
it('media filters and selection validation retain original authorization and file constraints',()=>{
 expect(new URLSearchParams(assetQuery({page:2,pageSize:50,q:' 酒 ',kind:'GIF',binding:'BOUND',square:true},true)).get('q')).toBe('酒')
 expect(new URLSearchParams(assetQuery({page:1,pageSize:50,binding:'BOUND'},false)).get('binding')).toBe('UNBOUND')
 expect(new URLSearchParams(assetQuery({page:1,pageSize:12},true)).get('pageSize')).toBe('20')
 expect(new URLSearchParams(assetQuery({page:1},true)).get('pageSize')).toBe('20')
 expect(selectionError({...asset('IMAGE'),availability:'MISSING'},{kind:'IMAGE'})).toContain('不可用')
 expect(selectionError(asset('GIF'),{kind:'IMAGE'})).toContain('类型不匹配')
 expect(selectionError({...asset('IMAGE'),width:0},{kind:'IMAGE',square:true})).toContain('方形')
 expect(selectionError({...asset('IMAGE'),height:400},{kind:'IMAGE',square:true})).toContain('方形')
 expect(selectionError(asset('IMAGE'),{kind:'IMAGE',square:true,excludedIds:['IMAGE']})).toContain('已选择')
 expect(selectionError(asset('IMAGE'),{kind:'IMAGE',square:true})).toBe('')
 expect(validateAssetFile({type:'image/png',size:1},'IMAGE')).toBe('')
 expect(validateAssetFile({type:'image/png',size:0},'IMAGE')).toContain('大于 0')
 expect(validateAssetFile({type:'image/png',size:11*1024*1024},'IMAGE')).toContain('10 MiB')
 expect(validateAssetFile({type:'video/mp4',size:51*1024*1024},'VIDEO')).toContain('50 MiB')
 expect(validateAssetFile({type:'image/gif',size:4},'IMAGE')).toContain('类型不匹配')
 expect(selectionStillCurrent('a','a')).toBe(true);expect(selectionStillCurrent('a','b')).toBe(false)
})
it('asset table requests only static thumbnails with lazy async decoding until explicit preview',async()=>{
 vi.mocked(api).mockResolvedValue({items:[asset('IMAGE'),asset('GIF'),asset('VIDEO',false)],total:3,page:1,pageSize:20})
 const w=mount(AssetBrowser,{props:{canRead:true,canUpload:false},global});wrappers.push(w);await flushPromises()
 expect(w.findAll('img').map(i=>i.attributes('src'))).toEqual(['/thumbnail/IMAGE','/thumbnail/GIF'])
 expect(w.find('video').exists()).toBe(false)
 for(const img of w.findAll('img')){expect(img.attributes('loading')).toBe('lazy');expect(img.attributes('decoding')).toBe('async')}
 await w.findAll('button').find(b=>b.text()==='预览')!.trigger('click')
 expect(w.get('[role=dialog] img').attributes('src')).toBe('/original/IMAGE')
})
it('picker uses static thumbnails and a failed thumbnail does not invalidate an available original asset',async()=>{
 vi.mocked(api).mockResolvedValue({items:[asset('GIF'),asset('VIDEO',false)],total:2,page:1,pageSize:20})
 const w=mount(AssetBrowser,{props:{canRead:true,canUpload:false,selecting:true,kind:'GIF'},global});wrappers.push(w);await flushPromises()
 expect(w.find('video').exists()).toBe(false)
 await w.get('img').trigger('error');expect(w.find('img').exists()).toBe(false)
 expect(w.text()).toContain('缩略图暂不可用')
 await w.findAll('button').find(b=>b.text()==='选择素材')!.trigger('click')
 expect(w.emitted('select')?.[0]?.[0]).toMatchObject({assetId:'GIF'})
 expect(w.findAll('img').some(img=>String(img.attributes('src')).startsWith('/original'))).toBe(false)
})
it('inventory SKU summary uses the thumbnail and retains a placeholder after thumbnail failure',async()=>{
 const w=mount(InventorySkuSummary,{props:{sku:{productName:'商品',skuCode:'sku',mainImage:asset('IMAGE')} as unknown as InventorySku}});wrappers.push(w)
 expect(w.get('img').attributes('src')).toBe('/thumbnail/IMAGE');expect(w.get('img').attributes('decoding')).toBe('async')
 await w.get('img').trigger('error');expect(w.find('img').exists()).toBe(false);expect(w.text()).toContain('图片暂不可用')
})
it('loading pagers report an unknown count and no page conclusion, then reveal actual pagination',async()=>{
 const p=mount(AdminListPager,{props:{page:1,pageSize:20,total:0,loading:true}});wrappers.push(p)
 const c=mount(AdminCursorPager,{props:{page:1,pageSize:20,hasPrevious:false,hasNext:false,loading:true}});wrappers.push(c)
 expect(p.text()).toContain('正在读取分页信息');expect(p.text()).not.toContain('共 0 条')
 expect(c.text()).toContain('正在读取分页信息');expect(c.text()).not.toContain('第 1 页')
 await p.setProps({loading:false,total:30});await flushPromises();expect(p.text()).toContain('共 30 条')
 await c.setProps({loading:false,hasNext:true});expect(c.text()).toContain('第 1 页');expect(c.findAll('button').find(b=>b.text()==='下一页')!.attributes('disabled')).toBeUndefined()
 await c.findAll('button').find(b=>b.text()==='下一页')!.trigger('click');expect(c.emitted('next')).toEqual([[]])
 await c.setProps({hasPrevious:true});await c.findAll('button').find(b=>b.text()==='上一页')!.trigger('click');expect(c.emitted('previous')).toEqual([[]])
 await c.get('select').setValue('50');expect(c.emitted('size')).toEqual([[50]])
})
it('release readiness is unknown during read and after failure, never a false unconfigured key or IP',async()=>{
 let reject!: (error:Error)=>void
 const pending=new Promise((_resolve,rejectRead)=>{reject=rejectRead})
 vi.mocked(api).mockImplementation(async path=>String(path).includes('/code-release/readiness')?await pending:{items:[],nextCursor:null})
 const account={accountId:'owner',permissionCodes:['code.version.read','code.release.manage']} as Account
 const w=mount(CodeVersionsView,{props:{account},global});wrappers.push(w);await flushPromises()
 expect(w.text()).toContain('正在检查发布条件');expect(w.text()).not.toContain('尚未配置');expect(w.text()).not.toContain('未满足的条件')
 reject(new Error('offline'));await flushPromises()
 expect(w.text()).toContain('发布条件读取失败');expect(w.text()).not.toContain('尚未配置');expect(w.text()).not.toContain('未满足的条件')
 const ready={appId:'wx1',versionId:null,egressIp:'203.0.113.5',checks:[],uploadKey:{configured:true,revision:1,appId:'wx1'},developerUploadKey:{configured:false,revision:0,appId:null},developerAppId:null}
 vi.mocked(api).mockResolvedValue(ready)
 await w.findAll('button').find(b=>b.text()==='重试')!.trigger('click');await flushPromises()
 expect(w.text()).toContain('已配置，可替换');expect(w.text()).toContain('203.0.113.5')
})
it('automatic editor canvas uses static thumbnails but explicit validated preview retains original media',async()=>{
 const config={pageType:'HOME',theme:{pageBackgroundColor:'#fff',headerBackgroundColor:'#fff',brandTextColor:'#000'},components:[{componentId:'image',type:'IMAGE',visible:true,sortOrder:0,props:{assetId:'gif1',ratio:'1:1'}}]} as PageConfig
 const w=mount(HomePagePreview,{props:{config,stale:false,interactive:true}});wrappers.push(w)
 expect(w.get('img').attributes('src')).toBe('/api/v1/admin/assets/gif1/thumbnail');expect(w.get('img').attributes('decoding')).toBe('async')
 await w.setProps({interactive:false});expect(w.get('img').attributes('src')).toBe('/api/v1/admin/assets/gif1/file')
})
it('read-only store accounts show loading and failure without rendering a zero-result table',async()=>{
 let reject!: (error:Error)=>void
 vi.mocked(api).mockImplementation(()=>new Promise((_resolve,rejectRead)=>{reject=rejectRead}))
 const w=mount(StoreAccountsView,{props:{account:{accountId:'reader',permissionCodes:['stores.accounts.read']} as Account},global});wrappers.push(w);await flushPromises()
 expect(w.text()).toContain('正在读取账户');expect(w.find('.admin-list-table').exists()).toBe(false);expect(w.text()).not.toContain('共 0 条')
 reject(new Error('网络读取失败'));await flushPromises()
 expect(w.text()).toContain('网络读取失败');expect(w.find('.admin-list-table').exists()).toBe(false);expect(w.text()).not.toContain('暂无符合条件')
})
it('dining table distinguishes pending reads, failed reads, and a successful empty result',async()=>{
 let reject!: (error:Error)=>void
 vi.mocked(api).mockImplementation(async path=>String(path).includes('/restaurants?')?await new Promise((_resolve,rejectRead)=>{reject=rejectRead}):[])
 const w=mount(RestaurantsView,{props:{account:{accountId:'reader',permissionCodes:['dining.read']} as Account},global:{...global,stubs:{...global.stubs,RestaurantDetailView:true,ElTable:{props:['data','emptyText'],template:'<div class="table-empty">{{emptyText}}</div>'},ElTableColumn:true}}});wrappers.push(w);await flushPromises()
 expect(w.get('.table-empty').text()).toContain('正在读取');expect(w.get('.table-empty').text()).not.toContain('暂无')
 reject(new Error('读取失败'));await flushPromises();expect(w.get('.table-empty').text()).toContain('读取失败')
 vi.mocked(api).mockResolvedValue({items:[],total:0});await w.findAll('button').find(b=>b.text()==='重试')!.trigger('click');await flushPromises()
 expect(w.get('.table-empty').text()).toContain('暂无餐饮店')
})
it('SPU and exchange tables use lightweight product thumbnails, never full images',async()=>{
 vi.mocked(api).mockImplementation(async path=>String(path).startsWith('/product-rows')?{rows:[{productId:'p1',name:'商品',productNo:'P1',mainImage:asset('IMAGE'),status:'DRAFT',fulfillmentKind:'SHIP',skuCount:1,onSaleSkuCount:0}],total:1,page:1,pageSize:20}:[])
 const product=mount(ProductManagement,{props:{account:{accountId:'reader',permissionCodes:['catalog.read']} as Account},global});wrappers.push(product);await flushPromises()
 expect(product.get('img').attributes('src')).toBe('/thumbnail/IMAGE');expect(product.get('img').attributes('loading')).toBe('lazy')
 await product.get('img').trigger('error');expect(product.find('img').exists()).toBe(false);expect(product.text()).toContain('图片暂不可用')
 vi.mocked(api).mockResolvedValue({items:[{id:'offer',productName:'商品',skuCode:'SKU',specs:[],status:'DRAFT',imageUrl:'/original/exchange',thumbnailUrl:'/thumbnail/exchange'}],pagination:{page:1,pageSize:20,total:1}})
 const exchange=mount(PointsExchangeView,{props:{account:{accountId:'reader',permissionCodes:['exchange.read']} as Account},global});wrappers.push(exchange);await flushPromises()
 expect(exchange.get('img').attributes('src')).toBe('/thumbnail/exchange');expect(exchange.get('img').attributes('decoding')).toBe('async')
})
it('staff and dining finance initial reads do not announce absent personnel or restaurants',async()=>{
 vi.mocked(api).mockImplementation(()=>new Promise(()=>{}))
 const s=mount(StaffPanel,{props:{restaurantId:'r1',canManage:false},global:{...global,stubs:{...global.stubs,ElTable:{props:['emptyText'],template:'<div>{{emptyText}}</div>'},ElTableColumn:true}}});wrappers.push(s)
 const f=mount(FinanceView,{props:{account:{accountId:'reader',permissionCodes:['dining.finance.read']} as Account},global});wrappers.push(f);await flushPromises()
 expect(s.text()).toContain('正在读取');expect(s.text()).not.toContain('尚未授权管理人员')
 expect(f.text()).toContain('正在读取餐饮店');expect(f.text()).not.toContain('暂无餐饮店')
})
it('carousel settings thumbnails avoid original files while the real hotzone drawing editor stays separate',()=>{
 const w=mount(PageComponentEditor,{props:{component:{componentId:'carousel',type:'CAROUSEL',visible:true,sortOrder:0,props:{slides:[{assetId:'banner',link:null}]}},canUpload:false,categories:[],products:[],pages:[]},global:{stubs:{AssetPicker:true,PageLinkEditor:true,HotzoneCanvas:true}}});wrappers.push(w)
 expect(w.get('img').attributes('src')).toBe('/api/v1/admin/assets/banner/thumbnail');expect(w.get('img').attributes('decoding')).toBe('async')
})
it('a failed product read retains its error without asserting no matching products or zero rows',async()=>{
 vi.mocked(api).mockRejectedValue(new Error('商品接口读取失败'))
 const w=mount(ProductManagement,{props:{account:{accountId:'reader',permissionCodes:['catalog.read']} as Account},global});wrappers.push(w);await flushPromises()
 expect(w.text()).toContain('商品接口读取失败');expect(w.text()).not.toContain('没有匹配');expect(w.text()).not.toContain('共 0 条')
})
