import {ref,watch,onMounted,onUnmounted} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import {api,ApiError,type Account,type Confirmation} from '../../api'
import {pendingCoupon,type PendingCoupon} from './form.mjs'
import {recoveryDecision,resultMatches,definitiveRejection} from './recovery.mjs'
export function useCouponOperation(account:()=>Account,target:()=>string,onSuccess:(result:unknown)=>Promise<void>|void,dirty:()=>boolean){
 const pending=ref<PendingCoupon|null>(null),busy=ref(false),error=ref(''),notice=ref(''),password=ref(''),storageError=ref(''),verified=ref(false)
 let epoch=0
 const stored=()=>pendingCoupon(sessionStorage,account().accountId,target())
 function restore(){password.value='';pending.value=null;verified.value=false;storageError.value='';try{pending.value=stored()}catch{storageError.value='浏览器存储不可用，无法保留操作身份，请恢复存储后操作。'}}
 function clear(){pendingCoupon(sessionStorage,account().accountId,target(),null);pending.value=null;verified.value=false}
 async function recover(){if(!pending.value||busy.value)return;const seq=epoch;busy.value=true;error.value='';verified.value=false;try{const r=await api<{status:'COMPLETED'|'NOT_FOUND';result?:unknown}>(`/coupon-operations/${pending.value.key}`);if(seq!==epoch)return;const decision=recoveryDecision(r,pending.value!);if(decision==='COMPLETED'){clear();notice.value='已读取上次操作的最终结果。';busy.value=false;await onSuccess(r.result)}else if(decision==='RETRY'){verified.value=true;notice.value='当前没有已完成记录，可使用原请求重试。'}else{error.value='服务端返回结果无法确认，原请求保持锁定，请重新读取结果。'}}catch(e){if(seq===epoch)error.value=e instanceof Error?e.message:'无法读取操作结果，请重试。'}finally{if(seq===epoch)busy.value=false}}
 async function write(path:string,method:'POST'|'PUT',body:Record<string,unknown>,action='',permission='coupon.manage'){
  if(busy.value||storageError.value||!account().permissionCodes.includes('coupon.read')||!account().permissionCodes.includes(permission)||pending.value&&!verified.value||action&&!password.value)return
  const seq=epoch,actor=account().accountId;busy.value=true;error.value='';let attempted=false
  try{
   if(!pending.value){const p={key:crypto.randomUUID(),path,method,body,action};pendingCoupon(sessionStorage,actor,target(),p);pending.value=p}
   const p=pending.value!;let token=''
   if(p.action){const c=await api<Confirmation>('/auth/confirm',{method:'POST',body:JSON.stringify({action:p.action,password:password.value,objectId:target().split(':')[0],revision:p.body.expectedRevision})});token=c.confirmationToken}
   if(seq!==epoch||actor!==account().accountId)return
   attempted=true;const result=await api<unknown>(p.path,{method:p.method,body:JSON.stringify(p.body),headers:{'Idempotency-Key':p.key,...(token?{'X-Action-Confirmation':token}:{})}})
   if(seq!==epoch)return;if(!resultMatches(p,result))throw new Error('服务端返回结果无法确认。');clear();notice.value='操作已完成。';busy.value=false;await onSuccess(result)
  }catch(e){if(seq!==epoch)return;if(e instanceof ApiError&&definitiveRejection(e.status)){if(attempted)clear();error.value=e.message+(e.status===409?' 请刷新最新修订后核对。':'')}else{if(!pending.value&&!attempted){storageError.value='浏览器存储不可用，操作尚未发送，请恢复存储后重试。';return}verified.value=false;error.value=`${e instanceof Error?e.message:'操作结果未知。'} 原请求已保留，先读取操作结果再重试。`}}finally{if(seq===epoch){password.value='';busy.value=false}}
 }
 function beforeUnload(e:BeforeUnloadEvent){if(dirty()||busy.value||pending.value){e.preventDefault();e.returnValue=''}}
 onMounted(()=>{restore();window.addEventListener('beforeunload',beforeUnload)})
 onUnmounted(()=>{epoch++;password.value='';window.removeEventListener('beforeunload',beforeUnload)})
 watch(()=>`${account().accountId}:${account().permissionCodes.join(',')}:${target()}`,()=>{epoch++;busy.value=false;error.value='';notice.value='';restore()})
 onBeforeRouteLeave(()=>!busy.value&&(!(dirty()||pending.value)||window.confirm(pending.value?'操作结果待确认，原请求已保留。确定离开？':'有未保存修改，确定离开？')))
 return {pending,busy,error,notice,password,storageError,verified,recover,write}
}
