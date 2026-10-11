const api = require('../../lib/api')
const dining = require('../../lib/dining-alliance')
const home = require('../../lib/home-page')
const startup = require('../../lib/startup')
Page({
 ...home,
 data:{...home.data,visit:null,diningVisit:null,busy:false,products:[],quantities:{},count:0},
 onLoad(options={}) {
  startup.completeLaunch()
  this.active=true;this.homeToken=0;this.identity=wx.getStorageSync('mall.memberToken')
  if(options.visit==='1'&&dining.current()) {this.loaded=true;this.setVisit(dining.current());return this.loadHome()}
  try{this.code=decodeURIComponent(options.scene||options.tableCode||'')}catch(_){this.code=''}
  return this.enter()
 },
 setVisit(visit) {
  const rect=wx.getMenuButtonBoundingClientRect?wx.getMenuButtonBoundingClientRect():null
  const info=wx.getWindowInfo?wx.getWindowInfo():null
  this.setData({visit,diningVisit:visit,store:null,storeError:'',...(rect?{headerPadding:rect.top,capsuleRight:info?info.windowWidth-rect.left+12:190}:{})})
 },
 onShow() {
  this.active=true
  if(this.hidden){this.hidden=false;this.setData({busy:false})}
  if(this.loaded&&!dining.current())this.expired()
  else if(this.loaded&&this.hiddenHome){this.hiddenHome=false;return this.loadHome()}
 },
 expired(){this.setData({state:'expired',visit:null,diningVisit:null,components:[],busy:false,error:'餐桌访问已结束，请重新扫描餐桌码。'})},
 onHide(){this.active=false;this.hidden=true;this.hiddenHome=true;this.generation=(this.generation||0)+1;home.onHide.call(this);if(!this.loaded&&!this.internal)dining.close();this.internal=false},
 onUnload(){this.onHide();const pages=typeof getCurrentPages==='function'?getCurrentPages():[];const route=pages.length?pages[pages.length-1].route:'';if(!dining.isInternalRoute(route))dining.close()},
 valid(g,identity){return this.active&&g===this.generation&&identity===wx.getStorageSync('mall.memberToken')},
 async enter(){
  if(this.data.busy)return
  if(!this.code){this.expired();return}
  if(!wx.getStorageSync('mall.memberToken')){this.setData({state:'auth',error:'请先登录，再重新扫描餐桌码。'});return}
  const identity=this.identity=wx.getStorageSync('mall.memberToken'),g=this.generation=(this.generation||0)+1
  this.setData({busy:true,state:'loading',error:'',visit:null,diningVisit:null,components:[],products:[],quantities:{},count:0})
  try{
   await dining.close()
   const visit=await api.post('/api/v1/app/dining-alliance/scan',{tableCode:this.code})
   if(!this.valid(g,identity)){await api.remove(`/api/v1/app/dining-alliance/sessions/${visit.sessionId}`);return}
   dining.open(visit);this.loaded=true;this.setVisit(visit);await this.loadHome()
  }catch(e){if(this.valid(g,identity))this.setData({state:e.statusCode===401?'auth':'error',error:e.message})}
  finally{if(this.valid(g,identity))this.setData({busy:false})}
 },
 loadHome(){if(!dining.current()){this.expired();return Promise.resolve()}return home.loadHome.call(this)},
 chooseStore(){},
 onShareAppMessage(){return {title:'商城首页',path:'/pages/home/home'}},
 onShareTimeline(){return {title:'商城首页'}},
 scan(){wx.scanCode({onlyFromCamera:true,success:r=>{const match=(r.path||'').match(/[?&]scene=([^&]+)/);if(!match){this.setData({error:'请扫描本小程序的餐桌码。'});return}try{this.code=decodeURIComponent(match[1]);this.active=true;this.enter()}catch(_){this.setData({error:'餐桌码格式无效。'})}}})}
})
