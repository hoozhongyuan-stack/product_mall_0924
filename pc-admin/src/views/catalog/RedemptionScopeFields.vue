<script setup lang="ts">
import {onUnmounted,ref,watch} from 'vue'
import {api} from '../../api'
import type {RedeemScope,RedeemStore} from '../../shared/fulfillment-policy'
const props=defineProps<{scope:RedeemScope;storeIds:string[];savedStores?:RedeemStore[];disabled?:boolean}>()
const emit=defineEmits<{ 'update:scope':[scope:RedeemScope];'update:storeIds':[ids:string[]] }>()
const stores=ref<RedeemStore[]>([]),loading=ref(false),error=ref('')
let epoch=0
async function load(){
 const current=++epoch;loading.value=true;error.value=''
 try{const result=await api<{items:RedeemStore[]}>('/products/redemption-stores');if(!Array.isArray(result.items))throw Error('授权前置仓列表不完整，请重新读取。');if(current===epoch)stores.value=result.items}
 catch(reason){if(current===epoch)error.value=reason instanceof Error?reason.message:'前置仓列表读取失败，请重试。'}
 finally{if(current===epoch)loading.value=false}
}
watch(()=>props.scope,scope=>{if(scope!=='PLATFORM'&&!stores.value.length&&!loading.value)void load()},{immediate:true})
onUnmounted(()=>epoch++)
function select(id:string,selected:boolean){emit('update:storeIds',selected?[...new Set([...props.storeIds,id])]:props.storeIds.filter(value=>value!==id))}
</script>
<template>
 <section class="redemption-scope-fields" aria-label="虚拟商品核销范围">
  <label>核销范围<select :value="scope" aria-label="核销范围" :disabled="disabled" @change="emit('update:scope',($event.target as HTMLSelectElement).value as RedeemScope)"><option value="PLATFORM">仅平台</option><option value="STORE">指定前置仓</option><option value="BOTH">平台＋指定前置仓</option></select></label>
  <template v-if="scope!=='PLATFORM'"><p v-if="loading" role="status">正在读取授权前置仓…</p><p v-if="error" class="error" role="alert">{{error}} <button type="button" class="text-button" :disabled="loading||disabled" @click="load">重新读取前置仓</button></p><div class="redemption-store-options"><label v-for="store in stores" :key="store.storeId" class="check-row"><input type="checkbox" :aria-label="`授权核销 ${store.name}`" :checked="storeIds.includes(store.storeId)" :disabled="disabled||loading" @change="select(store.storeId,($event.target as HTMLInputElement).checked)">{{store.name}}</label><label v-for="id in storeIds.filter(id=>!stores.some(store=>store.storeId===id))" :key="id" class="check-row"><input type="checkbox" checked :disabled="disabled||loading" @change="select(id,false)">{{savedStores?.find(store=>store.storeId===id)?.name || '历史授权前置仓'}}（当前不可新增）</label></div><p v-if="!loading&&!error&&!stores.length&&!storeIds.length">暂无可授权前置仓，请配置前置仓后重试，或选择仅平台。</p><p class="help-text">至少选择一个前置仓；各授权端共用同一份剩余权益。</p></template>
  <p class="help-text">配置仅影响新成交订单。已成交订单保留原核销范围和有效期。</p>
 </section>
</template>
<style scoped>
.redemption-scope-fields{display:grid;gap:12px;margin:16px 0;min-width:0}.redemption-scope-fields>label{display:grid;gap:8px;max-width:400px}.redemption-store-options{display:flex;gap:12px 24px;flex-wrap:wrap}.redemption-store-options label{overflow-wrap:anywhere;max-width:100%}.redemption-scope-fields p{margin:0}.redemption-scope-fields .check-row{display:flex;align-items:center;gap:8px}
</style>
