import {ref,onBeforeUnmount} from 'vue'
import {api} from '../../api'
import type {ShippingConfig} from './types'
export function useShippingConfig(){
 const config=ref<ShippingConfig|null>(null),loading=ref(false),busy=ref(false),error=ref(''),notice=ref(''),needsRead=ref(false),recoveryCounter=ref(0);let epoch=0
 async function load(){if(busy.value)return;const current=++epoch,recovering=needsRead.value;loading.value=true;error.value='';try{const result=await api<ShippingConfig>('/shipping/config');if(current===epoch){config.value=result;needsRead.value=false;if(recovering){recoveryCounter.value++;notice.value='已重新读取。请在列表中核对上次保存结果；原编辑窗口已关闭，避免重复创建。'}}}catch(e){if(current===epoch){config.value=null;error.value=e instanceof Error?e.message:'配送规则读取失败，请重试。'}}finally{if(current===epoch)loading.value=false}}
 async function write(path:string,method:string,body:Record<string,unknown>,message='配置已保存，仅影响后续新订单。'){
  if(busy.value||needsRead.value||!config.value)return false;const current=epoch;busy.value=true;error.value='';notice.value=''
  try{const result=await api<ShippingConfig>(`/shipping/${path}`,{method,body:JSON.stringify({...body,expectedRevision:config.value.revision})});if(current!==epoch)return false;config.value=result;notice.value=message;return true}catch(e){if(current===epoch){error.value=`${e instanceof Error?e.message:'保存结果未确认'}。请重新读取并核对后再操作。`;needsRead.value=true}return false}finally{if(current===epoch)busy.value=false}
 }
 function reset(){epoch++;config.value=null;loading.value=false;busy.value=false;needsRead.value=false;error.value='';notice.value=''}
 onBeforeUnmount(()=>epoch++)
 return {config,loading,busy,error,notice,needsRead,recoveryCounter,load,write,reset}
}
