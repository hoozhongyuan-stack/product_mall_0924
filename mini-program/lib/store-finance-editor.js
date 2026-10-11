const finance=require('./store-finance')
const {requestKey}=require('./orders')
const clearView=()=>({incomeRows:[],financeDetail:null,withdrawalConfirm:null})
module.exports={
 applyAccount(raw){const account=finance.present(raw);this.setData({account,incomeRows:account.income.filter(row=>!this.data.incomeFilter||row.status===this.data.incomeFilter),financeDetail:null})},
 filterIncome(e){const value=e.currentTarget.dataset.value;if(!this.data.canAccounts||!finance.INCOME_FILTERS.some(row=>row.value===value))return;this.setData({incomeFilter:value,incomeRows:(this.data.account?.income||[]).filter(row=>!value||row.status===value)})},
 openIncome(e){if(!this.data.canAccounts||!this.data.account)return;const row=this.data.account.income.find(row=>row.id===e.currentTarget.dataset.id);if(row)this.setData({financeDetail:row,accountPanel:'incomeDetail'})},
 openWithdrawal(e){if(!this.data.canAccounts||!this.data.account)return;const row=this.data.account.withdrawals.find(row=>row.id===e.currentTarget.dataset.id);if(row)this.setData({financeDetail:row,accountPanel:'withdrawDetail'})},
 backFinance(){this.setData({accountPanel:this.data.accountPanel==='incomeDetail'?'income':'history',financeDetail:null})},
 clearFinanceView(){this.confirmedDraft=null;this.setData(clearView())},
 allWithdrawal(){const available=this.data.account?.balance?.availableFen;if(!this.data.canAccounts||this.data.busy||this.pendingWithdrawal||this.confirmedDraft||!Number.isSafeInteger(available)||available<=0)return;this.setData({withdrawalForm:{...this.data.withdrawalForm,amount:(available/100).toFixed(2)}})},
 prepareWithdrawal(){
  if(this.data.busy||!this.data.canAccounts||!this.data.account?.withdrawalReady||!this.data.store)return
  if(this.pendingWithdrawal)return this.sendWithdrawal()
  try{
   const body=finance.withdrawal(this.data.withdrawalForm,this.data.account.balance?.availableFen,requestKey())
   this.confirmedDraft={body,storeId:this.data.store.id,identity:this.identity,generation:this.generation}
   this.setData({error:'',withdrawalConfirm:{amount:`¥${(body.amountFen/100).toFixed(2)}`,payeeName:body.payeeName,bankName:body.bankName,bankAccount:finance.maskBank(body.bankAccount)}})
  }catch(e){this.setData({error:e.message})}
 },
 cancelWithdrawal(){if(this.data.busy)return;this.confirmedDraft=null;this.setData({withdrawalConfirm:null})},
 async confirmWithdrawal(){
  const draft=this.confirmedDraft;if(!draft||this.data.busy)return
  if(draft.identity!==wx.getStorageSync('mall.memberToken')){this.clearFinance();this.setData({state:'auth',error:'登录状态已改变，请重新登录。'});return}
  if(!this.valid(draft.generation)||this.hidden||!this.data.canAccounts||this.data.store?.id!==draft.storeId){this.cancelWithdrawal();return}
  this.pendingWithdrawal={storeId:draft.storeId,identity:draft.identity,body:draft.body}
  this.confirmedDraft=null;this.setData({withdrawalConfirm:null});return this.sendWithdrawal()
 },
}
