<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import {api} from '../../api'
import {ElButton,ElInput,ElCheckbox,ElInputNumber} from 'element-plus'
import AdminListPager from '../../shared/list/AdminListPager.vue'
import type {Target} from './types'
const props=defineProps<{modelValue:string[];disabled?:boolean;weights?:boolean;catalogOnly?:boolean;weightValues?:Record<string,number|null>}>()
const emit=defineEmits<{'update:modelValue':[string[]];'update:weightValues':[Record<string,number|null>];targets:[Target[]]}>()
const search=ref(''),items=ref<Target[]>([]),page=ref(1),size=ref(20),total=ref(0),busy=ref(false),error=ref('');let epoch=0
async function load(){const current=++epoch;busy.value=true;error.value='';try{const r=await api<{items:Target[];total:number}>(`/shipping/targets?${new URLSearchParams({search:search.value.trim(),page:String(page.value),pageSize:String(size.value)})}`);if(current===epoch){items.value=r.items;total.value=r.total;emit('targets',r.items)}}catch(e){if(current===epoch)error.value=e instanceof Error?e.message:'商品读取失败，请重试。'}finally{if(current===epoch)busy.value=false}}
function choose(id:string,checked:boolean){emit('update:modelValue',checked?[...props.modelValue,id]:props.modelValue.filter(v=>v!==id))}
watch(()=>props.disabled,()=>{if(props.disabled){epoch++;busy.value=false}})
onBeforeUnmount(()=>epoch++)
load()
</script>
<template><section aria-label="关联商品选择"><form class="inline-fields" @submit.prevent="page=1;load()"><ElInput v-model="search" aria-label="搜索关联商品" maxlength="100" placeholder="商品名称或编号" :disabled="disabled"/><ElButton native-type="submit" :loading="busy" :disabled="disabled">查找商品</ElButton></form><p v-if="error" role="alert">{{error}}</p><p v-if="busy" role="status">正在读取商品…</p><div v-else class="target-results"><div v-for="p in items" :key="p.productId"><ElCheckbox v-if="!weights&&!catalogOnly" :model-value="modelValue.includes(p.productId)" :disabled="disabled" @update:model-value="choose(p.productId,!!$event)">{{p.productName}}</ElCheckbox><strong v-else-if="catalogOnly">{{p.productName}}</strong><template v-else><strong>{{p.productName}}</strong><div v-for="s in p.skus" :key="s.skuId" class="weight-row"><span>{{s.specLabel||s.skuCode}} / 每{{s.saleUnit}}<small v-if="s.weightGrams&&!s.weightConfigured" class="error">销售单位已变化，请重新填写重量</small><small v-if="!s.unitVersionId">销售单位未配置，无法填写重量</small></span><ElInputNumber :model-value="weightValues?.[s.skuId]??(s.weightConfigured?s.weightGrams:undefined)??undefined" :aria-label="`${p.productName} ${s.specLabel}重量克`" :min="1" :max="1000000" :precision="0" :disabled="disabled||!s.unitVersionId" @update:model-value="emit('update:weightValues',{...weightValues,[s.skuId]:$event??null})"/></div></template></div><p v-if="!items.length&&!error">暂无符合条件的商品。</p></div><AdminListPager :page="page" :page-size="size" :total="total" :loading="busy" @change="(p,s)=>{page=p;size=s;load()}"/><small v-if="!weights&&!catalogOnly">已选择 {{modelValue.length}} 个商品；留空作为默认模板。商品只能关联一个启用的指定模板。</small><small v-else-if="weights">按每个销售单位填写克重；修改销售单位后需重新配置。清空输入表示本次不修改，仅保存本次填写的规格。</small></section></template>
