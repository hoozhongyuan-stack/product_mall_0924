const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs')
const catalog = require('../lib/catalog'), { presentStorefront } = require('../lib/storefront')
test('category artwork resolves separately as square icon and wide banner', () => {
 const rows = catalog.categoryTree([{id:'r',name:'礼盒',status:'ACTIVE',imageUrl:'/api/v1/app/assets/icon/file'}, {id:'l',parentId:'r',name:'精选',status:'ACTIVE',bannerImageUrl:'/api/v1/app/assets/banner/file'}], 'https://shop.test')
 assert.equal(rows[0].imageUrl,'https://shop.test/api/v1/app/assets/icon/file')
 assert.equal(rows[0].children[0].bannerImageUrl,'https://shop.test/api/v1/app/assets/banner/file')
 assert.equal(catalog.categoryTree([{id:'r',status:'ACTIVE',imageUrl:'https://bad.test'}], 'https://shop.test')[0].imageUrl,'')
})
test('filing uses configured public data and hides empty or malformed content', () => {
 assert.equal(presentStorefront({filing:{recordNo:'  粤ICP备示例号  '}},'').recordNo,'粤ICP备示例号')
 assert.equal(presentStorefront(null,'').recordNo,'')
 assert.equal(presentStorefront({filing:{recordNo:42}},'').recordNo,'')
})
test('primary member page has no membership detail and secondary retains it', () => {
 const primary = fs.readFileSync('pages/member/index.wxml','utf8'), center = fs.readFileSync('pages/member/center.wxml','utf8')
 assert.doesNotMatch(primary,/member\.(grade|spendLabel|points|grades|rules)/)
 assert.match(primary,/openMemberCenter/); assert.match(primary,/show-record/)
 assert.match(center,/member.points.availablePoints/); assert.match(center,/member.grade.name/)
 assert.equal(require('../lib/member-route').memberReturnTarget({next:'center'}),'/pages/member/center')
})
function pageAt(file) {
 let definition; global.Page = value => { definition = value }; delete require.cache[require.resolve(file)]; require(file)
 return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
test('category switches select matching banner and keep warehouse product query', async () => {
 global.getApp = () => ({globalData:{apiBaseUrl:'https://shop.test'}})
 global.wx = { getStorageSync: () => '', request(r) {
  const values = r.url.includes('/categories') ? [{id:'r',status:'ACTIVE',name:'礼盒'},
   {id:'a',parentId:'r',status:'ACTIVE',name:'商务',bannerImageUrl:'/api/v1/app/assets/a/file'},
   {id:'b',parentId:'r',status:'ACTIVE',name:'节日',bannerImageUrl:'/api/v1/app/assets/b/file'},
   {id:'r2',status:'ACTIVE',name:'白酒'}] : {rows:[],total:0}
  r.success({statusCode:200,data:{success:true,data:values}})
 } }
 const page = pageAt('../pages/index/index.js'); await page.onLoad()
 assert.equal(page.data.activeBannerUrl,'https://shop.test/api/v1/app/assets/a/file')
 await page.selectLeaf({currentTarget:{dataset:{id:'b'}}})
 assert.equal(page.data.activeBannerUrl,'https://shop.test/api/v1/app/assets/b/file')
 page.bannerImageError({currentTarget:{dataset:{id:'b'}}}); assert.equal(page.data.failedBannerImages.b,true)
 page.categoryImageError({currentTarget:{dataset:{id:'r'}}}); assert.equal(page.data.failedCategoryImages.r,true)
 await page.selectRoot({currentTarget:{dataset:{id:'r2'}}}); assert.equal(page.data.activeBannerUrl,'')
 await page.selectLeaf({currentTarget:{dataset:{id:'unknown'}}}); assert.equal(page.data.activeLeafId,'')
})
test('member secondary selection is bounded and login resumes secondary page', async () => {
 const urls=[]; global.wx = {getStorageSync:()=>'',navigateTo:({url})=>urls.push(url)}
 const page=pageAt('../pages/member/center.js'); await page.onShow(); assert.equal(page.data.state,'auth')
 page.selectPanel({currentTarget:{dataset:{panel:'points'}}}); assert.equal(page.data.panel,'points')
 page.selectPanel({currentTarget:{dataset:{panel:'injected'}}}); assert.equal(page.data.panel,'points')
 page.login(); page.openPoints(); page.openConsumption()
 assert.deepEqual(urls,['/pages/login/login?returnTo=member&next=center','/pages/member/points','/pages/member/consumption'])
})
