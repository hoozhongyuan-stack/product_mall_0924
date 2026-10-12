const api=require('./api'),p=require('./procurement')
function erase(path){if(path&&wx.getFileSystemManager)wx.getFileSystemManager().unlink({filePath:path,fail(){}})}
function privateFile(path,id,owner){const valid=p.downloadPath(path,id);if(!valid||!owner||owner!==wx.getStorageSync('mall.memberToken'))throw new Error('付款凭证不可查看，请重新读取。');let task;const promise=new Promise((resolve,reject)=>{task=wx.downloadFile({url:api.baseUrl()+valid,header:{Authorization:`Bearer ${owner}`},timeout:20000,success:r=>{if(r.statusCode===200&&r.tempFilePath)resolve(r.tempFilePath);else{erase(r.tempFilePath);const e=new Error('付款凭证下载失败，请重试。');e.statusCode=r.statusCode;if(r.statusCode===401&&owner===wx.getStorageSync('mall.memberToken'))wx.removeStorageSync('mall.memberToken');reject(e)}},fail:()=>reject(new Error('付款凭证下载未完成，请重试。'))})});return {promise,cancel:()=>task&&task.abort&&task.abort()}}
function upload(path,id,owner){if(!path||!owner||owner!==wx.getStorageSync('mall.memberToken'))throw new Error('登录状态已改变，请重新进入前置仓中心。');let task;const promise=new Promise((resolve,reject)=>{task=wx.uploadFile({url:api.baseUrl()+p.root(id)+'/proofs',filePath:path,name:'file',header:{Authorization:`Bearer ${owner}`},timeout:20000,success:r=>{let v;try{v=JSON.parse(r.data)}catch(_){reject(new Error('付款凭证上传响应无效，请重新上传。'));return}if(r.statusCode>=200&&r.statusCode<300&&v&&v.success&&v.data&&p.UUID.test(v.data.assetId)&&p.downloadPath(v.data.previewUrl,id)){resolve(v.data);return}const e=new Error(v&&v.error&&v.error.message||'付款凭证上传失败，请重试。');e.statusCode=r.statusCode;if(r.statusCode===401&&owner===wx.getStorageSync('mall.memberToken'))wx.removeStorageSync('mall.memberToken');reject(e)},fail:()=>reject(new Error('付款凭证上传未完成，请检查网络后重试。'))})});return {promise,cancel:()=>task&&task.abort&&task.abort()}}
function pickerError(result) {
 const message=String(result&&result.errMsg||'')
 const cancelled=/cancel/i.test(message), denied=/auth|permission|deny|denied/i.test(message)
 const error=new Error(cancelled?'已取消选择，可重新选择付款凭证。':denied?'相册或相机权限未开启，请在微信设置中允许后重新选择。':'图片选择失败，请重新选择付款凭证。')
 error.code=cancelled?'PICKER_CANCELLED':denied?'PICKER_PERMISSION':'PICKER_FAILED'
 return error
}
function choose(){
 return new Promise((resolve,reject)=>{
  const success=result=>{
   const file=result.tempFiles&&result.tempFiles[0], path=file&&(file.tempFilePath||file.path)||(result.tempFilePaths&&result.tempFilePaths[0])
   if(!path){reject(pickerError({errMsg:'cancel'}));return}
   if(!file||!Number.isFinite(file.size)||file.size>5242880){reject(new Error('请选择不超过 5 MB 的付款凭证图片。'));return}
   resolve(path)
  }
  const fail=result=>reject(pickerError(result))
  try{
   if(wx.chooseMedia)wx.chooseMedia({count:1,mediaType:['image'],sizeType:['compressed'],sourceType:['album','camera'],success,fail})
   else if(wx.chooseImage)wx.chooseImage({count:1,sizeType:['compressed'],sourceType:['album','camera'],success,fail})
   else reject(new Error('当前微信版本暂不支持选择图片。'))
  }catch(error){reject(pickerError(error))}
 })
}
module.exports={erase,privateFile,upload,choose}
