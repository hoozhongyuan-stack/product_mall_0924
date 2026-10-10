const test = require('node:test')
const assert = require('node:assert/strict')
const finance = require('../lib/store-finance')
test('withdrawal amount converts decimal text to integer fen without floating point rounding', () => {
 assert.equal(finance.amountFen('92.10'),9210)
 assert.equal(finance.amountFen(' 0.01 '),1)
 for (const value of ['0','-1','1.001','1e2','NaN','1.','9999999999999999999']) assert.throws(()=>finance.amountFen(value),/金额/)
})
test('withdrawal validates required payee and bank fields and available balance',()=>{
 const fields={amount:'20.00',payeeName:' 张三 ',bankName:' 建设银行 ',bankAccount:'6222000000000000'}
 assert.deepEqual(finance.withdrawal(fields,2000,'key'),{requestKey:'key',amountFen:2000,payeeName:'张三',bankName:'建设银行',bankAccount:'6222000000000000'})
 assert.throws(()=>finance.withdrawal(fields,1999,'key'),/余额/)
 assert.throws(()=>finance.withdrawal({...fields,payeeName:''},2000,'key'),/收款人/)
 assert.throws(()=>finance.withdrawal({...fields,bankName:''},2000,'key'),/开户行/)
 assert.throws(()=>finance.withdrawal({...fields,bankAccount:'abc'},2000,'key'),/银行账号/)
})
test('account presentation preserves zero and unknown balances and real withdrawal states',()=>{
 const shown=finance.present({settlementReady:true,balance:{pendingFen:100,availableFen:0,frozenFen:200,paidFen:300},income:[{id:'i',paidFen:10000,costFen:6000,platformFen:800,freightFen:1000,storeFen:8200,status:'HELD'}],withdrawals:[{id:'w',amountFen:200,status:'APPROVED_PENDING_PAYMENT'}]})
 assert.equal(shown.available,'¥0.00');assert.equal(shown.income[0].storeAmount,'¥82.00');assert.equal(shown.income[0].statusLabel,'暂缓结算');assert.equal(shown.withdrawals[0].statusLabel,'已审核 · 待线下发放')
 assert.equal(finance.present({balance:null}).available,'暂不可用')
})
const storage = new Map()
global.wx = {getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k)}
const api = require('../lib/api')
function mount(){let config;global.Page=c=>config=c;delete require.cache[require.resolve('../pages/store-center/index.js')];require('../pages/store-center/index.js');return {...config,data:structuredClone(config.data),setData(v){this.data={...this.data,...v}}}}
function ready(){storage.clear();storage.set('mall.memberToken','member');const p=mount();p.identity='member';p.generation=1;p.data.store={id:'s'};p.data.section='account';p.data.canAccounts=true;p.data.account={withdrawalReady:true,balance:{availableFen:10000}};p.data.withdrawalForm={amount:'20',payeeName:'张三',bankName:'建设银行',bankAccount:'6222000000000000'};return p}
test('withdrawal uncertain request retries identical snapshot and disables store switching',async()=>{
 const p=ready(), oldPost=api.post, oldGet=api.get, attempts=[];api.get=async()=>({balance:{availableFen:8000},withdrawalReady:true});api.post=async(path,body)=>{attempts.push({path,body});if(attempts.length===1)throw new Error('network');return {id:'w'}};wx.showToast=()=>{}
 await p.requestWithdrawal();assert.equal(p.data.withdrawalUncertain,true);assert.equal(p.data.withdrawalForm.bankAccount,'');await p.sendWithdrawal();assert.deepEqual(attempts[0],attempts[1]);assert.equal(p.data.withdrawalUncertain,false);assert.equal(p.pendingWithdrawal,null);api.post=oldPost;api.get=oldGet
})
test('account clears private balance and bank form on hide and ignores response after member change',async()=>{
 const p=ready();p.onHide();assert.equal(p.data.account,null);assert.equal(p.data.withdrawalForm.bankAccount,'')
 const q=ready(),old=api.post;let finish;api.post=()=>new Promise(r=>finish=r);const pending=q.requestWithdrawal();storage.set('mall.memberToken','other');finish({id:'w'});await pending;assert.equal(q.data.account,null);assert.equal(q.pendingWithdrawal,null);api.post=old
})
test('withdrawal authoritative rejection permits correction without false success',async()=>{
 const p=ready(),old=api.post;api.post=async()=>{const e=new Error('余额改变');e.statusCode=409;throw e};await p.requestWithdrawal();assert.equal(p.pendingWithdrawal,null);assert.equal(p.data.withdrawalUncertain,false);assert.equal(p.data.error,'余额改变');api.post=old
})
test('in-flight withdrawal does not restore private account after hide and unknown retry blocks context changes',async()=>{
 const p=ready(),oldPost=api.post,oldGet=api.get;let finish;let reads=0;api.post=()=>new Promise(r=>finish=r);api.get=async()=>{reads++;return {balance:{availableFen:8000}}};const request=p.requestWithdrawal();p.onHide();finish({id:'w'});await request;assert.equal(p.data.account,null);assert.equal(reads,0)
 const q=ready();q.pendingWithdrawal={};q.data.stores=[{id:'other'}];q.selectStore({detail:{value:0}});assert.equal(q.data.store.id,'s');q.section({currentTarget:{dataset:{section:'products'}}});assert.equal(q.data.section,'account');api.post=oldPost;api.get=oldGet
})
test('invalid inputs prevent request and revoked account permission rejects submission',async()=>{
 const p=ready(),old=api.post;let writes=0;api.post=async()=>writes++;p.data.withdrawalForm.amount='1000';await p.requestWithdrawal();assert.match(p.data.error,/余额/);assert.equal(writes,0);p.data.canAccounts=false;await p.requestWithdrawal();assert.equal(writes,0);api.post=old
})
test('bank and payee boundaries match server contract and unload removes pending private snapshot',()=>{
 const fields={amount:'1',payeeName:'张'.repeat(80),bankName:'行'.repeat(120),bankAccount:'6'.repeat(34)}
 assert.equal(finance.withdrawal(fields,100,'key').amountFen,100)
 for(const patch of [{payeeName:'张'.repeat(81)},{bankName:'行'.repeat(121)},{bankAccount:'6'.repeat(35)}])assert.throws(()=>finance.withdrawal({...fields,...patch},100,'key'))
 assert.throws(()=>finance.amountFen('99000000.01'),/范围/)
 const p=ready();p.pendingWithdrawal={body:fields};p.onUnload();assert.equal(p.pendingWithdrawal,null);assert.equal(p.data.withdrawalForm.bankAccount,'')
})
