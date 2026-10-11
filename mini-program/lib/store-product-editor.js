const products = require('./store-products')
module.exports = {
 openStock(e) {
  if(this.data.busy || !this.data.canProducts)return
  const product=this.data.products.find(p=>p.id===e.currentTarget.dataset.id)
  if(product)this.setData({stockEditor:products.edit(product,{}),drafts:{},stockError:'',stockConflict:false,batchValue:'',batchOpen:false,stockHelp:false})
 },
 closeStock() { if(!this.data.busy)this.setData({stockEditor:null,drafts:{},stockError:'',stockConflict:false,batchOpen:false}) },
 swallow() {},
 stockInput(e) {
  if(this.data.busy || this.data.stockConflict)return
  const {product,pool}=e.currentTarget.dataset
  const source=this.data.products.find(p=>p.id===product)
  if(!source || !products.pools(source).some(s=>s.poolId===pool))return
  const values={...(this.data.drafts[product]||{}),[pool]:e.detail.value}
  this.setData({drafts:{...this.data.drafts,[product]:values},...(this.data.stockEditor&&this.data.stockEditor.id===product?{stockEditor:products.edit(source,values),stockError:''}:{})})
 },
 batchInput(e) { if(!this.data.busy)this.setData({batchValue:e.detail.value}) },
 toggleBatch() { if(!this.data.busy)this.setData({batchOpen:!this.data.batchOpen}) },
 toggleHelp() { this.setData({stockHelp:!this.data.stockHelp}) },
 applyBatch() {
  if(this.data.busy || this.data.stockConflict || !this.data.stockEditor)return
  try {const p=this.data.stockEditor;const values=products.batch(p,this.data.batchValue);this.setData({drafts:{[p.id]:values},stockEditor:products.edit(p,values),stockError:'',batchOpen:false})}
  catch(e){this.setData({stockError:e.message})}
 },
 saveStock() { if(this.data.stockEditor&&!this.data.stockConflict)return this.saveProduct({currentTarget:{dataset:{id:this.data.stockEditor.id}}}) },
 async reloadStock() { if(this.data.busy)return;this.closeStock();return this.loadSection() },
 filterProducts(e) {
  if(this.data.busy)return
  const value=e.currentTarget.dataset.filter
  if(!products.FILTERS.some(f=>f.value===value))return
  this.closeStock();this.setData({productFilter:value});return this.loadSection()
 },
 toggleProduct(e) {
  if(this.data.busy || !this.data.canProducts)return
  const p=this.data.products.find(p=>p.id===e.currentTarget.dataset.id)
  if(!p)return
  if(!p.onSale&&!p.canPutOnSale){this.setData({error:'平台已下架该商品，暂不可在本仓上架。'});return}
  const gen=this.generation,storeId=this.data.store.id
  const save=()=>{if(!this.hidden&&this.valid(gen)&&this.data.store.id===storeId)return this.saveProduct({currentTarget:{dataset:{id:p.id,toggle:true}}})}
  if(!p.onSale)return save()
  wx.showModal({title:'下架本仓商品？',content:'下架后本仓停止销售该商品，库存保留，已生成的订单仍需正常履约。',confirmText:'确认下架',confirmColor:'#a3202b',success:r=>{if(r.confirm)save()}})
 },
}
