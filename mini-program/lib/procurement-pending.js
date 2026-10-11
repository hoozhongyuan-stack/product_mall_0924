const KEY='mall.procurementPending.v1'
function copy(v){return JSON.parse(JSON.stringify(v))}
function save(v){const old=wx.getStorageSync(KEY);if(old&&old.owner===v.owner&&JSON.stringify(old)!==JSON.stringify(v))throw new Error('另有进货操作结果待确认，请先返回原前置仓重试。');wx.setStorageSync(KEY,copy(v))}
function read(id){const v=wx.getStorageSync(KEY);if(!v)return null;if(v.owner!==wx.getStorageSync('mall.memberToken')){clear();return null}return v.storeId===id?copy(v):null}
function clear(operation){const value=wx.getStorageSync(KEY);if(!operation||value&&value.owner===operation.owner&&value.path===operation.path&&value.body.requestKey===operation.body.requestKey)wx.removeStorageSync(KEY)}
module.exports={KEY,save,read,clear}
